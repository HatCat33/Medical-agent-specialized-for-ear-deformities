import { ConfigProvider, Input, Modal } from "antd";
import { Activity, Bell, Database, FlaskConical, LogOut, MessageSquareText, ShieldCheck } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { KnowledgeDashboard } from "../features/knowledge/KnowledgeDashboard";
import { type AdminSection, sectionNames } from "../features/knowledge/navigation";
import {
  ApiError,
  clearSecondaryAuthorization,
  configureSecondaryVerification,
  knowledgeApi,
  secondaryAuthorizationExpiry,
  secondarySecurityStatus,
  type SecondarySecurityStatus,
  verifySecondaryToken,
} from "../lib/api";

const TOKEN_KEY = "medical-agent-admin-token";

function sectionFromLocation(): AdminSection {
  const candidate = window.location.hash.replace(/^#\/?/, "");
  return candidate in sectionNames ? candidate as AdminSection : "overview";
}

function Login({ onAuthenticated }: { onAuthenticated: (token: string) => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const normalized = token.trim();
    if (!normalized) return;
    setSubmitting(true);
    setError("");
    try {
      await knowledgeApi(normalized, "/overview");
      sessionStorage.setItem(TOKEN_KEY, normalized);
      onAuthenticated(normalized);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "管理服务暂时不可用");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="admin-login">
      <form className="login-panel" onSubmit={submit}>
        <div className="login-brand" aria-hidden="true">
          <ShieldCheck size={24} />
        </div>
        <div>
          <h1>耳畸形管理后台</h1>
          <p>知识库运维</p>
        </div>
        <label htmlFor="admin-token">管理员令牌</label>
        <input
          id="admin-token"
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          autoComplete="current-password"
          placeholder="请输入管理员令牌"
        />
        {error && <div className="login-error">{error}</div>}
        <button type="submit" disabled={!token.trim() || submitting}>
          {submitting ? "正在验证..." : "进入管理端"}
        </button>
      </form>
    </main>
  );
}

export function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [section, setSection] = useState<AdminSection>(sectionFromLocation);
  const [security, setSecurity] = useState<SecondarySecurityStatus | null>(null);
  const [secondaryOpen, setSecondaryOpen] = useState(false);
  const [secondaryToken, setSecondaryToken] = useState("");
  const [secondaryError, setSecondaryError] = useState("");
  const [secondarySubmitting, setSecondarySubmitting] = useState(false);
  const waitingRef = useRef<Array<{ resolve: () => void; reject: (reason: Error) => void }>>([]);
  const icons = { overview: Activity, knowledge: Database, tests: FlaskConical, prompts: MessageSquareText, announcements: Bell };
  const authorizationExpiry = secondaryAuthorizationExpiry();

  useEffect(() => {
    if (!token) { setSecurity(null); return; }
    void secondarySecurityStatus(token).then(setSecurity).catch(() => setSecurity(null));
  }, [token]);

  useEffect(() => {
    const syncSection = () => setSection(sectionFromLocation());
    window.addEventListener("hashchange", syncSection);
    return () => window.removeEventListener("hashchange", syncSection);
  }, []);

  useEffect(() => {
    if (!token) { configureSecondaryVerification(null); return; }
    configureSecondaryVerification(() => new Promise<void>((resolve, reject) => {
      waitingRef.current.push({ resolve, reject });
      setSecondaryError("");
      setSecondaryOpen(true);
    }));
    return () => configureSecondaryVerification(null);
  }, [token]);

  useEffect(() => {
    if (!security?.verified || !authorizationExpiry) return;
    const remaining = new Date(authorizationExpiry).getTime() - Date.now();
    if (remaining <= 0) {
      clearSecondaryAuthorization();
      setSecurity(current => current ? { ...current, verified: false } : current);
      return;
    }
    const timer = window.setTimeout(() => {
      clearSecondaryAuthorization();
      setSecurity(current => current ? { ...current, verified: false } : current);
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [authorizationExpiry, security?.verified]);

  function openSecondaryVerification() {
    setSecondaryError("");
    setSecondaryOpen(true);
  }

  function cancelSecondaryVerification() {
    setSecondaryOpen(false);
    setSecondaryToken("");
    const cancellation = new ApiError("已取消二次验证，原操作未执行", 403, "admin_secondary_verification_cancelled");
    waitingRef.current.splice(0).forEach(item => item.reject(cancellation));
  }

  async function submitSecondaryVerification() {
    if (!secondaryToken.trim()) return;
    setSecondarySubmitting(true);
    setSecondaryError("");
    try {
      await verifySecondaryToken(token, secondaryToken.trim());
      setSecurity(current => ({ configured: current?.configured ?? true, verified: true, ttl_seconds: current?.ttl_seconds ?? 900 }));
      setSecondaryOpen(false);
      setSecondaryToken("");
      waitingRef.current.splice(0).forEach(item => item.resolve());
    } catch (reason) {
      setSecondaryError(reason instanceof ApiError ? reason.message : "二次验证暂时不可用");
    } finally {
      setSecondarySubmitting(false);
    }
  }

  function logout() {
    sessionStorage.removeItem(TOKEN_KEY);
    clearSecondaryAuthorization();
    setToken("");
  }

  function navigateToSection(nextSection: AdminSection) {
    if (nextSection === section) return;
    window.location.hash = nextSection;
  }

  return (
    <ConfigProvider
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          colorPrimary: "#147568",
          borderRadius: 6,
          fontFamily:
            'Inter, "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif',
        },
      }}
    >
      {!token ? (
        <Login onAuthenticated={setToken} />
      ) : (
        <div className="admin-shell">
          <header className="admin-topbar">
            <div className="admin-topbar-inner">
              <div className="admin-brand">
                <span className="admin-brand-mark"><ShieldCheck size={20} /></span>
                <div><strong>耳畸形管理后台</strong></div>
              </div>
              <nav className="admin-navigation" aria-label="管理功能">
                <div className="admin-navigation-inner">
                  {(Object.keys(sectionNames) as AdminSection[]).map(id => {
                    const Icon = icons[id];
                    return <button key={id} className={`nav-item ${section === id ? "is-active" : ""}`} type="button" onClick={() => navigateToSection(id)} aria-current={section === id ? "page" : undefined}>
                      <Icon size={18} /><span>{sectionNames[id]}</span>
                    </button>;
                  })}
                </div>
              </nav>
              <button className="logout-button" type="button" onClick={logout} title="退出管理端" aria-label="退出管理端">
                <LogOut size={18} />
              </button>
            </div>
          </header>
          <main className="admin-main">
            <KnowledgeDashboard token={token} section={section} security={security} authorizationExpiry={authorizationExpiry} onSecurityChanged={setSecurity} />
          </main>
          <Modal
            title="二次验证"
            open={secondaryOpen}
            okText="验证并继续"
            cancelText="取消"
            confirmLoading={secondarySubmitting}
            okButtonProps={{ disabled: !secondaryToken.trim() }}
            onOk={() => void submitSecondaryVerification()}
            onCancel={cancelSecondaryVerification}
          >
            <div className="secondary-verification-form">
              <p>该操作可能修改后台配置或业务数据，请输入二次验证令牌。</p>
              <label><span>二次验证令牌</span><Input.Password autoFocus value={secondaryToken} autoComplete="off" placeholder="请输入 .env 中配置的令牌" onChange={event => setSecondaryToken(event.target.value)} onPressEnter={() => void submitSecondaryVerification()} /></label>
              {secondaryError && <div className="login-error">{secondaryError}</div>}
            </div>
          </Modal>
        </div>
      )}
    </ConfigProvider>
  );
}

