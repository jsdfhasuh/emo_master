import { useMemo } from 'react';
import type { CSSProperties } from 'react';
import { parse } from 'postcss';
import selectorParser from 'postcss-selector-parser';
import type { BorderAppearance, ButtonStates, StyleBranch } from '../contracts/types';

export function scopeAppearanceCss(code: string, owner: string): { css: string; error?: string } {
    try {
        const document = parse(code, { map: false });
        const restriction = selectorParser().astSync(
            `:where([data-emo-owner=${JSON.stringify(owner)}][data-emo-part])`,
        ).first.first;
        document.each(rule => {
            if (rule.type === 'comment') return;
            if (rule.type !== 'rule') throw new Error('仅支持普通 CSS 规则，不支持 @ 规则');
            if (rule.nodes.some(node => node.type !== 'decl' && node.type !== 'comment'))
                throw new Error('CSS 规则内仅支持属性声明');
            rule.selector = selectorParser(selectors => {
                selectors.each(selector => {
                    if (!selector.length || selector.first.type === 'combinator' || selector.last.type === 'combinator'
                        || selector.nodes.some(node => node.type === 'nesting'))
                        throw new Error('选择器不完整或包含嵌套符号');
                    if (typeof CSS !== 'undefined' && !CSS.supports(`selector(${selector.toString().trim()})`))
                        throw new Error(`无法识别选择器：${selector.toString().trim()}`);
                    // Restrict the element receiving the declarations, including when an
                    // ancestor selector would otherwise reach a nested component.
                    const combinators = selector.nodes.filter(node => node.type === 'combinator');
                    const tail = selector.nodes.slice(selector.nodes.lastIndexOf(combinators[combinators.length - 1]) + 1);
                    const pseudoElement = tail.find(node => node.type === 'pseudo' &&
                        (node.value.startsWith('::') || [':before', ':after', ':first-letter', ':first-line'].includes(node.value)));
                    if (pseudoElement) selector.insertBefore(pseudoElement, restriction.clone());
                    else selector.append(restriction.clone());
                });
            }).processSync(rule.selector);
        });
        return { css: document.toString() };
    } catch (error) {
        return { css: '', error: error instanceof Error ? error.message : String(error) };
    }
}

export function AppearanceCss({ owner, branch }: { owner: string; branch?: StyleBranch }) {
    const code = branch?.enabled ? branch.code : '';
    const css = useMemo(() => code.trim() ? scopeAppearanceCss(code, owner).css : '', [code, owner]);
    return css ? <style>{css}</style> : null;
}

const baseProperties = {
    backgroundColor: 'background-color',
    borderColor: 'border-color',
    borderWidth: 'border-width',
    borderStyle: 'border-style',
    borderRadius: 'border-radius',
    padding: 'padding',
    color: 'color',
    fontFamily: 'font-family',
    fontSize: 'font-size',
    fontWeight: 'font-weight',
    lineHeight: 'line-height',
} as const;

/** Keep editable appearance in CSS rules and view geometry in the normal inline layout. */
export function appearancePart(owner: string | undefined, part: string, base?: CSSProperties, layout?: CSSProperties) {
    if (!owner) return { style: { ...base, ...layout } };
    const style: Record<string, string | number> = {};
    const names: string[] = [];
    for (const [key, value] of Object.entries(base ?? {})) {
        if (value == null) continue;
        const property = baseProperties[key as keyof typeof baseProperties];
        if (!property) throw new Error(`Unsupported base appearance property: ${key}`);
        names.push(property);
        style[`--emo-${property}`] = typeof value === 'number' && key !== 'fontWeight' && key !== 'lineHeight' ? `${value}px` : value;
    }
    return {
        'data-emo-owner': owner,
        'data-emo-part': part,
        'data-emo-base': names.length ? names.join(' ') : undefined,
        style: { ...style, ...layout } as CSSProperties,
    };
}

export function appearanceBorder(config: BorderAppearance | undefined, width: number, color: string): CSSProperties {
    return {
        borderWidth: config?.borderWidth ?? (config?.borderColor || config?.borderStyle ? Math.max(1, width) : width),
        borderStyle: config?.borderStyle ?? 'solid',
        borderColor: config?.borderColor ?? color,
    };
}

export function buttonStateStyle(states: ButtonStates | undefined): CSSProperties {
    if (!states) return {};
    const variables: Record<string, string> = {};
    for (const state of ['default', 'hover', 'pressed', 'disabled'] as const) {
        const settings = states[state];
        const values = {
            'background': settings?.background,
            'color': settings?.color,
            'border-color': settings?.borderColor,
            'border-width': settings?.borderWidth === undefined ? undefined : `${settings.borderWidth}px`,
            'border-style': settings?.borderStyle,
        };
        for (const [key, value] of Object.entries(values)) {
            // CSS variables inherit by default. Every button starts its own state values.
            variables[`--emo-${state}-${key}`] = value ?? 'initial';
        }
    }
    return variables as CSSProperties;
}
