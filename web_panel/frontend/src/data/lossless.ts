/** Preserve integer JSON literals outside IEEE-754's exact range for display.
 * This is not canonicalization: authoritative revisions are never recomputed here.
 * Large integers are exposed as decimal strings by default. History opts
 * into BigInt for integer cells so record keys can be sent back unchanged.
 */
export function parseLosslessJSON(text: string, preserveIntegerType = false): unknown {
    // History keys must make a round trip, including integers outside the
    // browser's exact number range. A NUL prefix cannot collide with any
    // original string unless it begins with an explicit JSON NUL escape.
    let prefix = '\0';
    if (preserveIntegerType && text.includes('\\u0000')) {
        const strings = (text.match(/"(?:\\[\s\S]|[^"\\])*"/g) ?? [])
            .filter(token => token.startsWith('"\\u0000')).map(token => JSON.parse(token) as string);
        while (strings.some(value => value.startsWith(prefix))) prefix += '\0';
    }
    const protectedText = text.replace(/"(?:\\[\s\S]|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g, token => {
        if (token[0] === '"') return token;
        if (!/[.eE]/.test(token) && (preserveIntegerType || !Number.isSafeInteger(Number(token))))
            return JSON.stringify(preserveIntegerType ? prefix + token : token);
        if (!Number.isFinite(Number(token))) return JSON.stringify(token);
        return token;
    });
    return JSON.parse(protectedText, preserveIntegerType
        ? (_key, value) => typeof value === 'string' && value.startsWith(prefix) ? BigInt(value.slice(prefix.length)) : value
        : undefined);
}
