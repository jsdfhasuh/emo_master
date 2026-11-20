import { useId } from 'react';
import type { CSSProperties, MouseEventHandler } from 'react';
import type { HostControl, PanelShell, TextStyle } from '../contracts/types';
import * as theme from '../../../contracts/panel-contract-v1/theme.json';
import { AppearanceCss, appearanceBorder, appearancePart, buttonStateStyle } from './advancedAppearance';
import { backgroundColor, textColor } from './presentation';

export function shellTextStyle(shell:PanelShell|undefined, role:'title'|'helper'):CSSProperties {
    const settings = shell?.headerTypography?.[role];
    return {
        fontSize: settings?.size ?? (role === 'title' ? 20 : 12),
        fontWeight: settings?.weight ?? (role === 'title' ? 'bold' : 'normal'),
        color: textColor(settings?.color ?? (role === 'title' ? 'default' : 'neutral')),
    };
}

export function shellHeaderStyle(shell?:PanelShell):CSSProperties {
    const surface = shell?.headerSurface;
    if (!surface) return {};
    const card = surface.kind === 'card';
    const background = surface.background ?? 'surface';
    const border = surface.border ?? (card ? 'subtle' : 'none');
    return {
        backgroundColor: backgroundColor(background),
        ...appearanceBorder(surface, border === 'subtle' ? 1 : 0, theme.border),
        borderRadius: surface.radius ?? (card ? 12 : 0),
        padding: `${surface.padding?.top ?? 12}px ${surface.padding?.right ?? 24}px ${surface.padding?.bottom ?? 12}px ${surface.padding?.left ?? 24}px`,
    };
}

export function hostControlStyle(control:HostControl|undefined, kind:'start'|'stop'):CSSProperties {
    const appearance = control?.appearance;
    const style = appearance?.style ?? (kind === 'start' ? 'primary' : 'secondary');
    const text:TextStyle|undefined = appearance?.text;
    return {
        '--host-control-background': style === 'primary' ? theme.primary : style === 'link' ? 'transparent' : theme.surface,
        '--host-control-color': text?.color ? textColor(text.color) : style === 'primary' ? '#ffffff' : style === 'link' ? theme.primary : theme.text,
        '--host-control-border': style === 'primary' ? theme.primary : style === 'link' ? 'transparent' : theme.border,
        '--host-control-hover-background': style === 'primary' ? theme.primary : theme.subtle,
        borderRadius: appearance?.radius ?? 8,
        padding: `${appearance?.padding?.top ?? 8}px ${appearance?.padding?.right ?? 16}px ${appearance?.padding?.bottom ?? 8}px ${appearance?.padding?.left ?? 16}px`,
        fontSize: text?.size ?? 13,
        fontWeight: text?.weight ?? 'normal',
        minWidth: appearance?.minWidth ?? 0,
        minHeight: appearance?.minHeight ?? 32,
    } as CSSProperties;
}

export function HostControlButton({control,kind,fallbackLabel,disabled,title,onClick}: {
    control?:HostControl; kind:'start'|'stop'; fallbackLabel:string; disabled:boolean;
    title:string; onClick:MouseEventHandler<HTMLButtonElement>;
}) {
    const owner = useId();
    const appearance = control?.appearance;
    const states = appearance?.buttonStates;
    const { borderRadius, padding, fontSize, fontWeight, ...layout } = hostControlStyle(control, kind);
    const variables = layout as Record<string, string>;
    const base = {
        borderRadius, padding, fontSize, fontWeight,
        backgroundColor: appearance?.background ?? (states && variables['--host-control-background']),
        color: states && variables['--host-control-color'],
        ...(states || appearance?.borderColor || appearance?.borderWidth !== undefined || appearance?.borderStyle
            ? appearanceBorder(appearance, 1, variables['--host-control-border']) : {}),
    };
    return <>
        <AppearanceCss owner={owner} branch={appearance?.customStyle?.webCss}/>
        <button className="operator-control" {...appearancePart(owner, 'action', base, {
            ...layout, ...buttonStateStyle(states), flexDirection: appearance?.iconPosition === 'right' ? 'row-reverse' : undefined,
            gap: appearance?.iconGap,
        })} data-emo-button-states={states ? '' : undefined} disabled={disabled} title={title} onClick={onClick}>
            {appearance?.showIcon && <svg aria-hidden="true" viewBox="0 0 16 16"
                width={appearance.iconSize ?? 14} height={appearance.iconSize ?? 14} fill="currentColor">
                {kind === 'start' ? <path d="M4 2L14 8L4 14Z"/> : <path d="M3 3H13V13H3Z"/>}
            </svg>}
            {control?.label ?? fallbackLabel}
        </button>
    </>;
}
