import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { CustomStyle } from '../contracts/types';
import { scopeAppearanceCss } from '../renderer/advancedAppearance';
import { themeStyle } from '../renderer/presentation';

export function AdvancedAppearanceDialog<Draft>({
    title, initial, readStyle, updateStyle, fields, preview, onConfirm, onClose,
}: {
    title: string; initial: Draft;
    readStyle: (draft: Draft) => CustomStyle | undefined;
    updateStyle: (draft: Draft, style: CustomStyle) => Draft;
    fields: (draft: Draft, change: (draft: Draft) => void) => ReactNode;
    preview: (draft: Draft) => ReactNode;
    onConfirm: (draft: Draft) => void; onClose: () => void;
}) {
    const [draft, setDraft] = useState(initial);
    const [selectors, setSelectors] = useState<string[]>([]);
    const heading = useId();
    const dialog = useRef<HTMLDialogElement>(null);
    const sample = useRef<HTMLDivElement>(null);
    const textarea = useRef<HTMLTextAreaElement>(null);
    const customStyle = readStyle(draft);
    const branch = customStyle?.webCss ?? {enabled:false, code:''};
    const diagnostic = useMemo(() => branch.enabled && branch.code.trim()
        ? scopeAppearanceCss(branch.code, 'preview-diagnostic').error : undefined, [branch.enabled, branch.code]);
    const updateBranch = (patch: Partial<typeof branch>) =>
        setDraft(updateStyle(draft, {...customStyle, webCss:{...branch,...patch}}));

    useLayoutEffect(() => {dialog.current?.showModal();}, []);
    useLayoutEffect(() => {
        const element = sample.current;
        if (!element) return;
        const refresh = () => {
            const parts = Array.from(element.querySelectorAll<HTMLElement>('[data-emo-part]'));
            const owner = parts.find(part=>part.dataset.emoPart==='root')?.dataset.emoOwner ?? parts[0]?.dataset.emoOwner;
            const hints = new Set<string>();
            for (const part of parts.filter(part=>part.dataset.emoOwner===owner)) {
                const base = `[data-emo-part="${part.dataset.emoPart}"]`;
                hints.add(base);
                if (part.matches('button[data-emo-button-states],button.panel-action,button.operator-control')) {
                    hints.add(base + ':enabled:hover');
                    hints.add(base + ':enabled:active');
                    hints.add(base + ':disabled');
                }
                if (part.hasAttribute('aria-current')) hints.add(base + '[aria-current="page"]');
                if (part.hasAttribute('aria-pressed')) hints.add(base + '[aria-pressed="true"]');
                if (part.hasAttribute('aria-selected') && part.dataset.emoPart === 'item') hints.add(base + '[aria-selected="true"]');
                if (part.dataset.emoPart === 'header') hints.add(base + ' th');
            }
            const next = Array.from(hints);
            setSelectors(old => old.length === next.length && old.every((hint,i)=>hint===next[i]) ? old : next);
        };
        refresh();
        const observer = new MutationObserver(refresh);
        observer.observe(element, { subtree:true, childList:true, attributes:true,
            attributeFilter:['data-emo-part','aria-current','aria-selected','aria-pressed'] });
        return () => observer.disconnect();
    }, []);
    return <dialog ref={dialog} className="editor-appearance-dialog" aria-labelledby={heading}
        onCancel={event=>{event.preventDefault();onClose();}} onClose={onClose}>
        <header><h2 id={heading}>高级外观 · {title}</h2>
            <button type="button" aria-label="关闭高级外观" onClick={onClose}>×</button></header>
        <div className="editor-appearance-body">
            <section className="editor-appearance-fields" aria-label="通用外观">
                {fields(draft,setDraft)}
                <details>
                    <summary>自定义 CSS</summary>
                    <label><input type="checkbox" checked={branch.enabled}
                        onChange={event=>updateBranch({enabled:event.target.checked})}/>启用自定义 CSS</label>
                    <label className="editor-appearance-code">CSS 代码<textarea ref={textarea} value={branch.code} spellCheck={false}
                        onChange={event=>updateBranch({code:event.target.value})}/></label>
                    <button type="button" onClick={()=>updateBranch({code:''})} disabled={!branch.code}>清空代码</button>
                    <details>
                        <summary>当前对象的选择器</summary>
                        <ul>{selectors.map(selector=><li key={selector}>
                            <button type="button" title="插入此选择器" onClick={()=>{
                                updateBranch({code:`${branch.code.trimEnd()}${branch.code.trim() ? '\n\n' : ''}${selector} {\n    /* 属性 */\n}`});
                                textarea.current?.focus();
                            }}><code>{selector}</code></button>
                        </li>)}</ul>
                    </details>
                </details>
            </section>
            <section className="editor-appearance-preview" aria-label="局部预览">
                <h3>局部预览</h3>
                {diagnostic && <p role="alert">当前代码未应用。{diagnostic}</p>}
                <div className="editor-appearance-sample" ref={sample} style={themeStyle}>{preview(draft)}</div>
            </section>
        </div>
        <footer>
            <button type="button" onClick={onClose}>取消</button>
            <button type="button" className="editor-primary" disabled={Boolean(diagnostic)} onClick={()=>onConfirm(draft)}>确定</button>
        </footer>
    </dialog>;
}
