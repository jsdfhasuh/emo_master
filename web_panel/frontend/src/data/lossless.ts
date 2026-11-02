/** Preserve integer JSON literals outside IEEE-754's exact range for display.
 * This is not canonicalization: authoritative revisions are never recomputed here.
 * Large integers are exposed as decimal strings, never silently rounded numbers.
 */
export function parseLosslessJSON(text: string): unknown {
    const protectedText = text.replace(/"(?:\\[\s\S]|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g, token => {
        if (token[0] === '"') return token;
        if (!/[.eE]/.test(token) && !Number.isSafeInteger(Number(token))) return JSON.stringify(token);
        if (!Number.isFinite(Number(token))) return JSON.stringify(token);
        return token;
    });
    return JSON.parse(protectedText);
}
