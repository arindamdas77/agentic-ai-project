import { useEffect, useState } from 'react';
import { Bot, ShieldCheck, Terminal, Activity, Send, Check, X, Clock3, Play, Loader2, ListChecks, ChevronDown, ChevronUp, RotateCcw, FileText, AlertTriangle, Server } from 'lucide-react';
const API = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const STATUS_LABEL = {
  awaiting_approval: 'Awaiting approval',
  in_progress: 'In progress',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  rejected: 'Rejected',
  finished_with_issues: 'Finished with issues',
};

const VIEWS = [
  { id: 'overview', label: 'Overview', icon: Activity },
  { id: 'tasks', label: 'DevOps tasks', icon: Terminal },
  { id: 'approvals', label: 'Approvals', icon: ShieldCheck },
  { id: 'history', label: 'Activity history', icon: Clock3 },
];

function Composer({ prompt, setPrompt, busy, onSubmit }) {
  return (
    <form className="composer" onSubmit={onSubmit}>
      <textarea
        value={prompt}
        onChange={e => setPrompt(e.target.value)}
        placeholder="Describe a DevOps task, e.g. check disk usage, running Docker containers, or show docker logs..."
      />
      <div className="composer-foot">
        <span><ShieldCheck size={15} /> Read-only diagnostics · approval required</span>
        <button disabled={busy || !prompt.trim()}>
          {busy ? 'Planning...' : 'Create task'} <Send size={16} />
        </button>
      </div>
    </form>
  );
}

function EnvBanner({ env }) {
  if (!env) return null;
  const rt = env.runtime || {};
  const degraded = (env.warnings || []).length > 0;
  return (
    <div className={`env-banner${degraded ? ' degraded' : ''}`}>
      <Server size={16} />
      <div>
        <b>
          Backend: {rt.environment || 'unknown'} · {env.os?.pretty_name || ''} · {rt.user || ''}
          {env.docker?.cli_available ? ' · docker OK' : ' · docker missing'}
          {env.systemd?.journalctl_available ? ' · journalctl OK' : ' · journalctl missing'}
        </b>
        {(env.warnings || []).map((w, i) => <p key={i}><AlertTriangle size={12} /> {w}</p>)}
      </div>
    </div>
  );
}

function StepMeta({ step }) {
  return (
    <div className="step-meta">
      <span className="tool-badge"><Terminal size={12} /> {step.tool}</span>
      {step.params && Object.keys(step.params).length > 0 && (
        <span className="tool-badge params">{JSON.stringify(step.params)}</span>
      )}
      <span className={`step-status st-${step.status}`}>{STATUS_LABEL[step.status] || step.status}</span>
      {step.duration_ms != null && <span className="step-status">{step.duration_ms} ms</span>}
      {step.retry_count > 0 && <span className="step-status">retry ×{step.retry_count}</span>}
    </div>
  );
}

function ApprovalInfo({ step }) {
  if (step.approved_by) {
    return <p className="approval-info">Approved by {step.approved_by} at {step.approved_at ? new Date(step.approved_at).toLocaleString() : '—'}</p>;
  }
  if (step.rejected_by) {
    return <p className="approval-info muted">Rejected by {step.rejected_by} at {step.rejected_at ? new Date(step.rejected_at).toLocaleString() : '—'}</p>;
  }
  return null;
}

function StepActions({ step, running, onDecide }) {
  return (
    <div className="actions">
      <button
        className="approve"
        disabled={running}
        onClick={() => onDecide(step, true)}
      >
        {running ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
        Approve & run
      </button>
      <button
        className="reject"
        disabled={running}
        onClick={() => onDecide(step, false)}
      >
        <X size={15} /> Reject
      </button>
    </div>
  );
}

function OutputBlock({ step, open, onToggle }) {
  const hasOutput = !!step.output;
  const hasError = !!step.error;
  if (!hasOutput && !hasError) return null;
  return (
    <div className="output-wrap">
      <button className="output-toggle" onClick={onToggle}>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        {open ? 'Hide output' : 'Show output'}
        {hasError && <span className="tag-error">error</span>}
      </button>
      {open && (
        <>
          {hasOutput && <pre className="output">{step.output}</pre>}
          {hasError && <pre className="output error">{step.error}</pre>}
        </>
      )}
    </div>
  );
}

function StepRow({ step, taskId, runningStep, retryingStep, outputOpen, onToggleOutput, onDecide, onRetry }) {
  const running = runningStep === step.id;
  return (
    <div className="step">
      <div className="step-body">
        <b>{step.title}</b>
        <p>{step.description}</p>
        <StepMeta step={step} />
        <ApprovalInfo step={step} />
        {step.status === 'awaiting_approval' && (
          <StepActions step={step} running={running} onDecide={(s, approved) => onDecide(taskId, s, approved)} />
        )}
        {step.status === 'failed' && (
          <div className="actions">
            <button className="approve retry" disabled={retryingStep === step.id} onClick={() => onRetry(taskId, step)}>
              {retryingStep === step.id ? <Loader2 size={15} className="spin" /> : <RotateCcw size={15} />}
              Retry (requires approval)
            </button>
          </div>
        )}
        <OutputBlock step={step} open={outputOpen} onToggle={() => onToggleOutput(step.id)} />
        {!outputOpen && step.output && !step.error && (
          <pre className="output">{step.output.slice(0, 500)}</pre>
        )}
        {step.status === 'completed' && !step.output && (
          <p className="result">Diagnostic finished (no output).</p>
        )}
        {step.status === 'rejected' && (
          <p className="result muted">Step rejected — nothing was executed.</p>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState('overview');
  const [prompt, setPrompt] = useState('');
  const [task, setTask] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [busy, setBusy] = useState(false);
  const [runningStep, setRunningStep] = useState(null);
  const [retryingStep, setRetryingStep] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [openOutputs, setOpenOutputs] = useState({});
  const [detail, setDetail] = useState(null);
  const [env, setEnv] = useState(null);
  const [report, setReport] = useState(null);
  const [showReport, setShowReport] = useState(false);

  async function loadTasks() {
    try {
      const r = await fetch(`${API}/tasks`);
      if (r.ok) setTasks(await r.json());
    } catch { /* API unreachable — views show empty state */ }
  }

  async function loadEnv() {
    try {
      const r = await fetch(`${API}/tasks/environment`);
      if (r.ok) setEnv(await r.json());
    } catch { /* environment probe optional */ }
  }

  useEffect(() => { loadTasks(); loadEnv(); }, []);

  function upsertTask(updated) {
    setTasks(prev => {
      const idx = prev.findIndex(t => t.id === updated.id);
      if (idx === -1) return [updated, ...prev];
      const next = [...prev];
      next[idx] = updated;
      return next;
    });
    setTask(prev => (prev && prev.id === updated.id ? updated : prev));
  }

  async function createTask(e) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true);
    try {
      const r = await fetch(`${API}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      if (!r.ok) throw new Error('API request failed');
      const created = await r.json();
      upsertTask(created);
      setTask(created);
      setPrompt('');
      setView('tasks');
    } catch {
      alert('Cannot reach the API. Start the backend and try again.');
    } finally {
      setBusy(false);
    }
  }

  async function decide(taskId, step, approved) {
    setRunningStep(step.id);
    try {
      const r = await fetch(`${API}/tasks/${taskId}/actions/${step.id}/approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      });
      if (r.ok) {
        const updated = await r.json();
        upsertTask(updated);
        loadTasks();
      }
    } finally {
      setRunningStep(null);
    }
  }

  async function retry(taskId, step) {
    setRetryingStep(step.id);
    try {
      const r = await fetch(`${API}/tasks/${taskId}/actions/${step.id}/retry`, { method: 'POST' });
      if (r.ok) {
        const updated = await r.json();
        upsertTask(updated);
        loadTasks();
      }
    } finally {
      setRetryingStep(null);
    }
  }

  function toggleOutput(stepId) {
    setOpenOutputs(prev => ({ ...prev, [stepId]: !prev[stepId] }));
  }

  async function toggleDetail(t) {
    if (expanded === t.id) { setExpanded(null); setDetail(null); return; }
    setExpanded(t.id);
    setDetail(null);
    try {
      const r = await fetch(`${API}/tasks/${t.id}`);
      if (r.ok) setDetail(await r.json());
    } catch { /* ignore */ }
  }

  async function toggleReport(t) {
    if (showReport && report?.task_id === t.id) { setShowReport(false); setReport(null); return; }
    setShowReport(true);
    setReport(null);
    try {
      const r = await fetch(`${API}/tasks/${t.id}/report`);
      if (r.ok) setReport(await r.json());
    } catch { /* ignore */ }
  }

  const pending = tasks.flatMap(t =>
    t.steps.filter(s => s.status === 'awaiting_approval').map(s => ({ task: t, step: s }))
  );
  const decided = tasks.flatMap(t =>
    t.steps.filter(s => ['completed', 'failed', 'rejected'].includes(s.status))
      .map(s => ({ task: t, step: s }))
  ).slice(0, 10);
  const stats = {
    total: tasks.length,
    pending: pending.length,
    completed: tasks.filter(t => t.status === 'completed').length,
    issues: tasks.filter(t => t.status === 'finished_with_issues').length,
  };

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><Bot size={25} /><span>Personal AI Agent</span></div>
        {VIEWS.map(v => (
          <div
            key={v.id}
            className={`nav${view === v.id ? ' active' : ''}`}
            onClick={() => setView(v.id)}
          >
            <v.icon size={18} /> {v.label}
            {v.id === 'approvals' && pending.length > 0 && <span className="nav-badge">{pending.length}</span>}
          </div>
        ))}
        <div className="side-note">
          <ShieldCheck size={18} />
          <b>Approval-first mode</b>
          <p>Plans use read-only diagnostics only. Each step runs after you approve it. No arbitrary commands.</p>
        </div>
      </aside>

      <section className="content">
        <header>
          <div>
            <div className="eyebrow">PERSONAL WORKSPACE</div>
            <h1>AI Agent Dashboard</h1>
            <p>Describe a task. Review the plan. Run safe diagnostics.</p>
          </div>
          <span className="status"><i /> API online</span>
        </header>

        <EnvBanner env={env} />

        {view === 'overview' && (
          <>
            <section className="hero">
              <div>
                <div className="eyebrow light">YOUR ASSISTANT</div>
                <h2>What should I help you with?</h2>
                <p>Check disk, memory, Docker, services, logs and ports — you approve every step.</p>
              </div>
              <Bot className="hero-icon" size={76} />
            </section>

            <Composer prompt={prompt} setPrompt={setPrompt} busy={busy} onSubmit={createTask} />

            <div className="stats">
              <article className="stat"><ListChecks size={20} /><b>{stats.total}</b><p>Total tasks</p></article>
              <article className="stat"><ShieldCheck size={20} /><b>{stats.pending}</b><p>Awaiting approval</p></article>
              <article className="stat"><Check size={20} /><b>{stats.completed}</b><p>Completed</p></article>
              <article className="stat"><Activity size={20} /><b>{stats.issues}</b><p>Finished with issues</p></article>
            </div>

            <div className="section-title">Recent activity</div>
            {tasks.length === 0 && <p className="empty">No tasks yet. Describe a diagnostic above to get started.</p>}
            {tasks.slice(0, 5).map(t => (
              <div className="hist-row" key={t.id} onClick={() => { setTask(t); setView('tasks'); }}>
                <div>
                  <b>{t.prompt}</b>
                  <span className="hist-meta">{t.steps.length} steps · {t.planner || 'plan'}</span>
                </div>
                <span className="pending">{(t.status || '').replaceAll('_', ' ')}</span>
              </div>
            ))}
          </>
        )}

        {view === 'tasks' && (
          <>
            <Composer prompt={prompt} setPrompt={setPrompt} busy={busy} onSubmit={createTask} />

            {!task && <p className="empty">No task selected. Create one above or pick one from Overview / Activity history.</p>}

            {task && (
              <section className="task-card">
                <div className="task-head">
                  <div>
                    <div className="eyebrow">TASK PLAN{task.planner ? ` · ${task.planner}` : ''}</div>
                    <h3>{task.prompt}</h3>
                  </div>
                  <div className="head-right">
                    <span className="pending">{(task.status || '').replaceAll('_', ' ')}</span>
                    <button className="report-btn" onClick={() => toggleReport(task)}>
                      <FileText size={14} /> Report
                    </button>
                  </div>
                </div>

                {showReport && report?.task_id === task.id && (
                  <pre className="output report">{report.report}</pre>
                )}
                {showReport && !report && <p className="empty">Loading report…</p>}

                {task.steps.map((step, idx) => (
                  <div className="step" key={step.id}>
                    <div className="step-number">{idx + 1}</div>
                    <StepRow
                      step={step}
                      taskId={task.id}
                      runningStep={runningStep}
                      retryingStep={retryingStep}
                      outputOpen={!!openOutputs[step.id]}
                      onToggleOutput={toggleOutput}
                      onDecide={(tid, s, approved) => decide(tid, s, approved)}
                      onRetry={retry}
                    />
                  </div>
                ))}
              </section>
            )}
          </>
        )}

        {view === 'approvals' && (
          <>
            <div className="section-title">Pending approvals ({pending.length})</div>
            {pending.length === 0 && (
              <p className="empty">Nothing waiting for approval. Create a task and its steps will appear here.</p>
            )}
            {pending.map(({ task: t, step }) => (
              <section className="task-card" key={`${t.id}-${step.id}`}>
                <div className="task-head">
                  <div>
                    <div className="eyebrow">FROM TASK · {t.id.slice(0, 8)}</div>
                    <h3>{t.prompt}</h3>
                  </div>
                </div>
                <StepRow
                  step={step}
                  taskId={t.id}
                  runningStep={runningStep}
                  retryingStep={retryingStep}
                  outputOpen={!!openOutputs[step.id]}
                  onToggleOutput={toggleOutput}
                  onDecide={(tid, s, approved) => decide(tid, s, approved)}
                  onRetry={retry}
                />
              </section>
            ))}

            <div className="section-title">Completed approvals ({decided.length})</div>
            {decided.length === 0 && <p className="empty">No decided steps yet.</p>}
            {decided.map(({ task: t, step }) => (
              <div className="hist-row" key={`${t.id}-${step.id}`} onClick={() => { setTask(t); setView('tasks'); }}>
                <div>
                  <b>{step.title}</b>
                  <span className="hist-meta">{t.prompt.slice(0, 60)} · {step.tool}</span>
                </div>
                <span className={`pending st-${step.status}`}>{STATUS_LABEL[step.status] || step.status}</span>
              </div>
            ))}
          </>
        )}

        {view === 'history' && (
          <>
            <div className="section-title">Activity history</div>
            {tasks.length === 0 && <p className="empty">No tasks recorded yet.</p>}
            {tasks.map(t => (
              <section className="task-card" key={t.id}>
                <div className="task-head clickable" onClick={() => toggleDetail(t)}>
                  <div>
                    <div className="eyebrow">{t.created_at ? new Date(t.created_at).toLocaleString() : ''} · {t.planner || 'plan'}</div>
                    <h3>{t.prompt}</h3>
                  </div>
                  <div className="head-right">
                    <span className="pending">{(t.status || '').replaceAll('_', ' ')}</span>
                    {expanded === t.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </div>
                </div>
                {expanded === t.id && (
                  <div className="hist-detail">
                    {!detail && <p className="empty">Loading…</p>}
                    {detail && detail.steps.map((step, idx) => (
                      <div className="step" key={step.id}>
                        <div className="step-number">{idx + 1}</div>
                        <StepRow
                          step={step}
                          taskId={detail.id}
                          runningStep={runningStep}
                          retryingStep={retryingStep}
                          outputOpen={!!openOutputs[step.id]}
                          onToggleOutput={toggleOutput}
                          onDecide={(tid, s, approved) => decide(tid, s, approved)}
                          onRetry={retry}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </section>
            ))}
          </>
        )}

        <div className="section-title">Designed for your workflow</div>
        <div className="tiles">
          <article><Terminal /><b>DevOps automation</b><p>Disk, memory, CPU, Docker, services, logs and ports.</p></article>
          <article><ShieldCheck /><b>Approval control</b><p>Every diagnostic step needs your approval before it runs.</p></article>
          <article><Activity /><b>Execution history</b><p>Plans, approvals, outputs and errors in one place.</p></article>
        </div>
      </section>
    </main>
  );
}
