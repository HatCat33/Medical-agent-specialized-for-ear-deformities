import { ConfigProvider } from "antd";
import { Activity, Database, FileText, FlaskConical, ListChecks, LogOut, MessageSquareText, ShieldCheck, Volume2 } from "lucide-react";
import { type FormEvent, useState } from "react";

import { KnowledgeDashboard } from "../features/knowledge/KnowledgeDashboard";
import { type AdminSection, sectionNames } from "../features/knowledge/navigation";
import { ApiError, knowledgeApi } from "../lib/api";

const TOKEN_KEY = "medical-agent-admin-token";

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
          <h1>医疗科普助手管理端</h1>
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
  const [section, setSection] = useState<AdminSection>("overview");
  const icons = { overview: Activity, versions: Database, chunks: FileText, tests: FlaskConical, prompts: MessageSquareText, speech: Volume2, operations: ListChecks };

  function logout() {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken("");
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
                <div><strong>医疗科普助手</strong><span>管理控制台</span></div>
              </div>
              <nav className="admin-navigation" aria-label="管理功能">
                <div className="admin-navigation-inner">
                  {(Object.keys(sectionNames) as AdminSection[]).map(id => {
                    const Icon = icons[id];
                    return <button key={id} className={`nav-item ${section === id ? "is-active" : ""}`} type="button" onClick={() => setSection(id)} aria-current={section === id ? "page" : undefined}>
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
            <KnowledgeDashboard token={token} section={section} />
          </main>
        </div>
      )}
    </ConfigProvider>
  );
}

