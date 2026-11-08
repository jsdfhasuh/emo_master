import type { Me } from '../contracts/types';

export function LocalModeWarning({me}: {me?: Me}) {
    return <p role="note" className="alert">{me && me.authMode !== 'local' ? '服务仍开放本机免密入口。' : ''}本机免密管理员，可控制全部已登记 Runtime；退出不关闭免密入口，停用请停止服务或移除 --local-no-auth。</p>;
}
