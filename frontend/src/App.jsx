import { useState } from 'react';
import { Bot, ShieldCheck, Terminal, Activity, Send, Check, X, Clock3 } from 'lucide-react';
const API = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';
export default function App() {
  const [prompt, setPrompt] = useState('');
  const [task, setTask] = useState(null);
  const [busy, setBusy] = useState(false);
  async function createTask(e) {
    e.preventDefault(); if (!prompt.trim()) return;
    setBusy(true);
    try { const r = await fetch(`${API}/tasks`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({prompt})}); if (!r.ok) throw new Error('API request failed'); setTask(await r.json()); }
    catch { alert('Cannot reach the API. Start the backend and try again.'); }
    finally { setBusy(false); }
  }
  async function approve(step, approved) {
    const r = await fetch(`${API}/tasks/${task.id}/actions/${step.id}/approval`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({approved})});
    if (r.ok) setTask(await r.json());
  }
  return <main className="shell">
    <aside className="sidebar"><div className="brand"><Bot size={25}/><span>Personal AI Agent</span></div><div className="nav active"><Activity size={18}/> Overview</div><div className="nav"><Terminal size={18}/> DevOps tasks</div><div className="nav"><ShieldCheck size={18}/> Approvals</div><div className="nav"><Clock3 size={18}/> Activity history</div><div className="side-note"><ShieldCheck size={18}/><b>Approval-first mode</b><p>Every action requires your individual approval. This starter version does not execute commands.</p></div></aside>
    <section className="content"><header><div><div className="eyebrow">PERSONAL WORKSPACE</div><h1>AI Agent Dashboard</h1><p>Describe a task. Review the plan. Stay in control.</p></div><span className="status"><i/> API starter</span></header>
      <section className="hero"><div><div className="eyebrow light">YOUR ASSISTANT</div><h2>What should I help you with?</h2><p>Start with a DevOps task or ask for help with your daily work.</p></div><Bot className="hero-icon" size={76}/></section>
      <form className="composer" onSubmit={createTask}><textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="Example: Check disk usage on my Ubuntu server and suggest cleanup steps..."/><div className="composer-foot"><span><ShieldCheck size={15}/> No action runs without approval</span><button disabled={busy || !prompt.trim()}>{busy?'Planning...':'Create task'} <Send size={16}/></button></div></form>
      {task && <section className="task-card"><div className="task-head"><div><div className="eyebrow">TASK PLAN</div><h3>{task.prompt}</h3></div><span className="pending">{task.status.replaceAll('_',' ')}</span></div>{task.steps.map(step=><div className="step" key={step.id}><div className="step-number">1</div><div className="step-body"><b>{step.title}</b><p>{step.description}</p><div className="step-status">Status: {step.status.replaceAll('_',' ')}</div>{step.approval===null?<div className="actions"><button className="approve" onClick={()=>approve(step,true)}><Check size={15}/> Approve plan</button><button className="reject" onClick={()=>approve(step,false)}><X size={15}/> Reject</button></div>:<p className="result">{step.approval==='approved'?'Plan approval recorded. Execution remains disabled in this starter build.':'Plan rejected.'}</p>}</div></div>)}</section>}
      <div className="section-title">Designed for your workflow</div><div className="tiles"><article><Terminal/><b>DevOps automation</b><p>Linux, Docker, Jenkins, AWS and Git workflows.</p></article><article><ShieldCheck/><b>Approval control</b><p>Review each proposed action before it can run.</p></article><article><Activity/><b>Execution history</b><p>Track plans, approvals, outcomes and errors.</p></article></div>
    </section>
  </main>
}
