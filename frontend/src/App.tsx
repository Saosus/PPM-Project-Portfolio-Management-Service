import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, json } from './api';

type User = { id: number; username: string; first_name: string; last_name: string; email: string; role: string; is_active: boolean };
type Project = {
  id: number; code: string | null; title: string; description: string; department: string; strategic_goal: string;
  expected_benefit: string; budget: string; effort: string; status: string; status_label: string;
  created_by: User; assigned_experts: string[]; rating: string | null; rank_order: number | null;
  rejection_reason: string; created_at: string; submitted_at: string | null;
};
type Criterion = { id: number; name: string; description: string; weight: string; reverse_direction: boolean; active: boolean };
type ScenarioItem = { id: number; code: string | null; title: string; budget: string; expected_benefit: string; rating: string | null; status: string; included: boolean; rank_order: number | null };
type Scenario = { scenario: { id: number; name: string; budget_limit: string; status: string; status_label: string; submitted_at?: string | null; finalized_at?: string | null }; items: ScenarioItem[]; totals: { used: string; remainder: string; percentage: string; total_benefit: string; within_budget: boolean } };
type AuditEvent = { id: number; created_at: string; username: string | null; ip_address: string | null; event_type: string; object_type: string; object_id: string; before_data: unknown; after_data: unknown; status: string; message: string };
type Protocol = { scenario: Scenario['scenario']; projects: Array<{ id: number; code: string; title: string; status: string; for: number; against: number; abstain: number; quorum_reached: boolean }> };
type View = 'requests' | 'criteria' | 'scenarios' | 'assessment' | 'committee' | 'protocols' | 'audit';

const roleLabels: Record<string, string> = {
  INITIATOR: 'Инициатор', EXPERT: 'Эксперт', PORTFOLIO_MANAGER: 'Менеджер портфеля',
  COMMITTEE_MEMBER: 'Член комитета', ADMIN: 'Администратор',
};
const statusClass = (status: string) => `status status-${status.toLowerCase()}`;
const fmt = (value: string | number | null | undefined) => value == null ? '—' : String(value);

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [view, setView] = useState<View>('requests');
  const [requests, setRequests] = useState<Project[]>([]);
  const [criteria, setCriteria] = useState<Criterion[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [logs, setLogs] = useState<AuditEvent[]>([]);
  const [protocols, setProtocols] = useState<Protocol[]>([]);
  const [login, setLogin] = useState('initiator');
  const [password, setPassword] = useState('initiator123');
  const [requestForm, setRequestForm] = useState({ title: '', description: '', department: 'ИТ-служба', strategic_goal: 'Повышение надёжности цифровых сервисов', expected_benefit: '10.00', budget: '5.00', effort: '2.0' });
  const [editingProject, setEditingProject] = useState<number | null>(null);
  const requestFormRef = useRef<HTMLFormElement>(null);
  const submitOnCreateRef = useRef(false);
  const [assignments, setAssignments] = useState<Record<number, string[]>>({});
  const [criteriaDraft, setCriteriaDraft] = useState<Array<{ name: string; description: string; weight: string; reverse_direction: boolean }>>([]);
  const [scenarioForm, setScenarioForm] = useState({ name: 'Сценарий 2027', budget_limit: '30.00' });
  const [assessmentProject, setAssessmentProject] = useState('');
  const [scores, setScores] = useState<Record<number, string>>({});
  const [comments, setComments] = useState<Record<number, string>>({});
  const [auditFilters, setAuditFilters] = useState({ username: '', event_type: '' });

  const isManager = user?.role === 'PORTFOLIO_MANAGER' || user?.role === 'ADMIN';
  const tabs = useMemo((): Array<{ id: View; label: string }> => {
    if (!user) return [];
    const items: Array<{ id: View; label: string }> = [{ id: 'requests', label: 'Заявки' }];
    if (user.role === 'EXPERT') items.push({ id: 'assessment', label: 'Экспертная оценка' });
    if (isManager) items.push({ id: 'criteria', label: 'Критерии' }, { id: 'scenarios', label: 'Моделирование' }, { id: 'protocols', label: 'Протоколы' });
    if (user.role === 'COMMITTEE_MEMBER') items.push({ id: 'committee', label: 'Голосование' }, { id: 'protocols', label: 'Протоколы' });
    if (user.role === 'ADMIN') items.push({ id: 'audit', label: 'Аудит' });
    return items;
  }, [user, isManager]);

  const loadData = useCallback(async (current: User) => {
    setLoading(true);
    try {
      const reqs = await api<Project[]>('requests/');
      setRequests(reqs);
      if (['EXPERT', 'PORTFOLIO_MANAGER', 'ADMIN'].includes(current.role)) {
        const result = await api<Criterion[]>('criteria/');
        setCriteria(result);
        setCriteriaDraft(result.map(c => ({ name: c.name, description: c.description, weight: c.weight, reverse_direction: c.reverse_direction })));
      }
      if (['PORTFOLIO_MANAGER', 'ADMIN'].includes(current.role)) {
        setUsers(await api<User[]>('users/'));
      }
      if (['PORTFOLIO_MANAGER', 'ADMIN', 'COMMITTEE_MEMBER'].includes(current.role)) {
        setScenarios(await api<Scenario[]>('scenarios/'));
      }
      if (['PORTFOLIO_MANAGER', 'ADMIN', 'COMMITTEE_MEMBER'].includes(current.role)) {
        setProtocols(await api<Protocol[]>('protocols/'));
      }
      if (current.role === 'ADMIN') setLogs(await api<AuditEvent[]>('admin/logs/'));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить данные');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const stored = localStorage.getItem('ppm_token');
    if (!stored) return;
    api<User>('auth/me/').then(async current => {
      setUser(current);
      await loadData(current);
    }).catch(() => localStorage.removeItem('ppm_token'));
  }, [loadData]);

  useEffect(() => {
    if (!tabs.some(t => t.id === view)) setView(tabs[0]?.id ?? 'requests');
  }, [tabs, view]);

  const runAction = async (action: () => Promise<unknown>, success: string, reload = true) => {
    setBusy(true); setError(''); setMessage('');
    try {
      await action();
      setMessage(success);
      if (reload && user) await loadData(user);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Операция завершилась ошибкой');
    } finally {
      setBusy(false);
    }
  };

  const doLogin = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const response = await api<{ token: string; user: User }>('auth/login/', { method: 'POST', body: json({ username: login, password }) });
      localStorage.setItem('ppm_token', response.token);
      setUser(response.user); setPassword(''); setView('requests');
      await loadData(response.user);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка входа');
    } finally { setBusy(false); }
  };

  const logout = () => {
    localStorage.removeItem('ppm_token'); setUser(null); setRequests([]); setCriteria([]); setScenarios([]); setLogs([]); setProtocols([]); setMessage(''); setError('');
  };

  const submitDraft = (id: number) => runAction(() => api(`requests/${id}/submit/`, { method: 'POST', body: json({}) }), 'Заявка отправлена и зарегистрирована.');
  const createRequest = async (event: FormEvent) => {
    event.preventDefault();
    const submitAfterSave = submitOnCreateRef.current;
    submitOnCreateRef.current = false;
    const wasEditing = editingProject !== null;
    await runAction(async () => {
      if (wasEditing) {
        await api(`requests/${editingProject}/`, { method: 'PATCH', body: json(requestForm) });
        setEditingProject(null);
      } else {
        const project = await api<Project>('requests/', { method: 'POST', body: json(requestForm) });
        if (submitAfterSave) {
          await api(`requests/${project.id}/submit/`, { method: 'POST', body: json({}) });
        }
      }
      setRequestForm({ title: '', description: '', department: 'ИТ-служба', strategic_goal: 'Повышение надёжности цифровых сервисов', expected_benefit: '10.00', budget: '5.00', effort: '2.0' });
    }, wasEditing ? 'Изменения сохранены.' : submitAfterSave ? 'Заявка создана и зарегистрирована.' : 'Черновик сохранён.');
  };

  const editRequest = (project: Project) => {
    setRequestForm({ title: project.title, description: project.description, department: project.department, strategic_goal: project.strategic_goal, expected_benefit: project.expected_benefit, budget: project.budget, effort: project.effort });
    setEditingProject(project.id);
    setError(''); setMessage(''); setView('requests');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const saveCriteria = () => runAction(() => api('criteria/', { method: 'POST', body: json({ criteria: criteriaDraft }) }), 'Шаблон критериев сохранён.');
  const assignExperts = (projectId: number) => runAction(() => api(`requests/${projectId}/assign/`, { method: 'POST', body: json({ experts: assignments[projectId] ?? [] }) }), 'Эксперты назначены.');
  const rejectRequest = async (projectId: number) => {
    const reason = window.prompt('Укажите причину отклонения (не менее 10 символов):');
    if (reason === null) return;
    if (reason.trim().length < 10) {
      setError('Причина отклонения должна содержать не менее 10 символов.');
      return;
    }
    await runAction(() => api(`requests/${projectId}/reject/`, { method: 'POST', body: json({ reason }) }), 'Заявка отклонена.');
  };
  const createScenario = (event: FormEvent) => {
    event.preventDefault();
    return runAction(() => api('scenarios/', { method: 'POST', body: json(scenarioForm) }), 'Сценарий создан.');
  };
  const autoSelect = (scenarioId: number) => runAction(() => api(`scenarios/${scenarioId}/auto-select/`, { method: 'POST', body: json({}) }), 'Автоподбор выполнен.');
  const toggleItem = (scenarioId: number, item: ScenarioItem, included: boolean) => runAction(() => api(`scenarios/${scenarioId}/toggle/`, { method: 'POST', body: json({ project_id: item.id, included }) }), 'Состав сценария обновлён.');
  const submitScenario = (scenarioId: number) => runAction(() => api(`scenarios/${scenarioId}/submit/`, { method: 'POST', body: json({}) }), 'Сценарий передан инвестиционному комитету.');
  const vote = (scenarioId: number, projectId: number, decision: string) => runAction(() => api('committee/vote/', { method: 'POST', body: json({ scenario_id: scenarioId, project_id: projectId, decision }) }), 'Голос сохранён.');
  const finalize = (scenarioId: number) => runAction(() => api('committee/finalize/', { method: 'POST', body: json({ scenario_id: scenarioId }) }), 'Решение комитета зафиксировано.');

  const saveAssessment = async (event: FormEvent) => {
    event.preventDefault();
    if (!assessmentProject) { setError('Выберите заявку для оценки'); return; }
    await runAction(async () => {
      for (const c of criteria) {
        const score = Number(scores[c.id]);
        if (!Number.isInteger(score) || score < 1 || score > 5) throw new Error(`Укажите целую оценку 1–5 для критерия «${c.name}»`);
        await api('assessments/', { method: 'POST', body: json({ project: Number(assessmentProject), criterion: c.id, score, comment: comments[c.id] ?? '' }) });
      }
    }, 'Оценки сохранены. Рейтинг будет рассчитан после оценок всех назначенных экспертов.');
  };

  const downloadCsv = async () => {
    await runAction(async () => {
      const blob = await api<Blob>('export/portfolio.csv');
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = 'portfolio.csv'; a.click(); URL.revokeObjectURL(url);
    }, 'CSV-файл сформирован.', false);
  };

  const loadFilteredLogs = async () => {
    await runAction(async () => {
      const query = new URLSearchParams();
      if (auditFilters.username) query.set('username', auditFilters.username);
      if (auditFilters.event_type) query.set('event_type', auditFilters.event_type);
      setLogs(await api<AuditEvent[]>(`admin/logs/?${query.toString()}`));
    }, 'Журнал обновлён.', false);
  };

  if (!user) return <main className="login-page">
    <section className="login-card">
      <div className="brand-mark">PPM</div>
      <p className="eyebrow">COMPANY N · учебный стенд</p>
      <h1>Управление портфелем ИТ-проектов</h1>
      <p className="muted">Подача заявок, экспертная оценка и бюджетное моделирование.</p>
      {error && <div className="alert error">{error}</div>}
      <form onSubmit={doLogin} className="stack">
        <label>Логин<input value={login} onChange={e => setLogin(e.target.value)} autoComplete="username" required /></label>
        <label>Пароль<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></label>
        <button disabled={busy}>{busy ? 'Входим…' : 'Войти в систему'}</button>
      </form>
      <details className="demo-details"><summary>Демо-учётные записи</summary><p>initiator / initiator123 · manager / manager123 · expert1 / expert12345</p><p>committee1 / committee123 · admin / admin12345</p></details>
      <p className="fine-print">Демонстрационные учётные записи предназначены только для локального учебного стенда.</p>
    </section>
  </main>;

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand-row"><span className="brand-mark small">P</span><div><strong>PPM</strong><small>Company N</small></div></div>
      <div className="side-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav>{tabs.map(tab => <button key={tab.id} className={view === tab.id ? 'nav-link active' : 'nav-link'} onClick={() => setView(tab.id)}>{tab.label}</button>)}</nav>
      <div className="sidebar-bottom"><div className="user-chip"><span className="avatar">{user.first_name?.[0] ?? user.username[0]}</span><div><strong>{user.first_name || user.username}</strong><small>{roleLabels[user.role] ?? user.role}</small></div></div><button className="logout" onClick={logout}>Выйти</button></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div><p className="eyebrow">КОМПАНИЯ N / PPM</p><h1>{tabs.find(t => t.id === view)?.label ?? 'Панель'}</h1></div><div className="topbar-actions"><span className="secure-dot">● API online</span><button className="secondary" disabled={loading || busy} onClick={() => user && loadData(user)}>Обновить</button></div></header>
      {error && <div className="alert error" role="alert">{error}</div>}
      {message && <div className="alert success" role="status">{message}</div>}
      {(loading || busy) && <div className="progress-line" />}

      {view === 'requests' && <>
        <section className="stats-grid">
          <article className="stat-card"><span>Всего заявок</span><strong>{requests.length}</strong><small>Доступные в вашей роли</small></article>
          <article className="stat-card"><span>На оценке</span><strong>{requests.filter(r => r.status === 'UNDER_EVALUATION').length}</strong><small>Экспертная работа</small></article>
          <article className="stat-card"><span>Утверждено</span><strong>{requests.filter(r => r.status === 'APPROVED').length}</strong><small>Решение комитета</small></article>
        </section>
        {user.role === 'INITIATOR' && <section className="panel"><div className="panel-heading"><div><h2>{editingProject !== null ? 'Редактирование заявки' : 'Новая заявка'}</h2><p>Заявку можно изменить до назначения экспертов.</p></div><span className="tag">М1 · Управление заявками</span></div>
          <form ref={requestFormRef} onSubmit={createRequest} className="form-grid">
            <label className="wide">Название проекта (5–200 символов)<input minLength={5} maxLength={200} value={requestForm.title} onChange={e => setRequestForm({ ...requestForm, title: e.target.value })} required /></label>
            <label className="wide">Описание (20–5000 символов)<textarea minLength={20} maxLength={5000} rows={3} value={requestForm.description} onChange={e => setRequestForm({ ...requestForm, description: e.target.value })} required /></label>
            <label>Подразделение<input value={requestForm.department} onChange={e => setRequestForm({ ...requestForm, department: e.target.value })} required /></label>
            <label>Стратегическая цель<input value={requestForm.strategic_goal} onChange={e => setRequestForm({ ...requestForm, strategic_goal: e.target.value })} required /></label>
            <label>Ожидаемая выгода, млн руб.<input type="number" min="0.01" max="100000" step="0.01" value={requestForm.expected_benefit} onChange={e => setRequestForm({ ...requestForm, expected_benefit: e.target.value })} required /></label>
            <label>Бюджет, млн руб.<input type="number" min="0.01" max="100000" step="0.01" value={requestForm.budget} onChange={e => setRequestForm({ ...requestForm, budget: e.target.value })} required /></label>
            <label>Трудозатраты, чел.-мес.<input type="number" min="0.5" max="10000" step="0.5" value={requestForm.effort} onChange={e => setRequestForm({ ...requestForm, effort: e.target.value })} required /></label>
            <div className="wide form-footer"><span className="muted tiny">Валидация повторно выполняется на сервере.</span><div className="button-pair">{editingProject !== null && <button type="button" className="secondary" onClick={() => { setEditingProject(null); setRequestForm({ title: '', description: '', department: 'ИТ-служба', strategic_goal: 'Повышение надёжности цифровых сервисов', expected_benefit: '10.00', budget: '5.00', effort: '2.0' }); }}>Отмена</button>}{editingProject === null && <button type="button" className="secondary" disabled={busy} onClick={() => { const form = requestFormRef.current; if (form?.reportValidity()) { submitOnCreateRef.current = true; form.requestSubmit(); } }}>Создать и отправить</button>}<button disabled={busy}>{editingProject !== null ? 'Сохранить изменения' : 'Сохранить черновик'}</button></div></div>
          </form>
        </section>}
        <section className="panel"><div className="panel-heading"><div><h2>{user.role === 'INITIATOR' ? 'Мои заявки' : 'Реестр проектов'}</h2><p>Статус и расчётный рейтинг обновляются с сервера.</p></div><span className="count-pill">{requests.length}</span></div>
          <div className="table-wrap"><table><thead><tr><th>Код / проект</th><th>Подразделение</th><th>Бюджет</th><th>Выгода</th><th>Рейтинг</th><th>Статус</th>{isManager && <th>Назначение</th>}{user.role === 'INITIATOR' && <th>Действие</th>}</tr></thead><tbody>
            {requests.map(project => <tr key={project.id}><td><strong>{project.code ?? 'Черновик'}</strong><span className="table-subtitle">{project.title}</span></td><td>{project.department}</td><td>{project.budget}</td><td>{project.expected_benefit}</td><td><strong>{fmt(project.rating)}</strong></td><td><span className={statusClass(project.status)}>{project.status_label}</span></td>
              {isManager && <td>{project.status === 'REGISTERED' ? <div className="assign-box"><select multiple aria-label={`Эксперты для ${project.code}`} value={assignments[project.id] ?? []} onChange={e => setAssignments({ ...assignments, [project.id]: Array.from(e.target.selectedOptions, o => o.value) })}>{users.filter(u => u.role === 'EXPERT').map(u => <option key={u.id} value={u.username}>{u.username}</option>)}</select><div className="button-pair"><button className="compact" onClick={() => assignExperts(project.id)}>Назначить</button><button className="compact secondary" onClick={() => rejectRequest(project.id)}>Отклонить</button></div></div> : <span className="muted tiny">{project.assigned_experts.join(', ') || '—'}</span>}</td>}
              {user.role === 'INITIATOR' && <td><div className="button-pair">{['DRAFT', 'REGISTERED'].includes(project.status) && <button className="compact secondary" onClick={() => editRequest(project)}>Изменить</button>}{project.status === 'DRAFT' && <button className="compact" onClick={() => submitDraft(project.id)}>Отправить</button>}{!['DRAFT', 'REGISTERED'].includes(project.status) && <span className="muted tiny">Недоступно</span>}</div></td>}
            </tr>)}
            {!requests.length && <tr><td colSpan={isManager ? 7 : 7} className="empty">Заявок пока нет.</td></tr>}
          </tbody></table></div>
        </section>
        {isManager && <div className="toolbar"><button className="secondary" onClick={downloadCsv}>Экспорт утверждённых проектов в CSV</button><span className="muted tiny">UTF-8 BOM · разделитель «;» · 10 столбцов</span></div>}
      </>}

      {view === 'criteria' && isManager && <section className="panel"><div className="panel-heading"><div><h2>Шаблон экспертной оценки</h2><p>Сумма весов должна быть равна 1.0000 с допуском 0.0001.</p></div><span className="tag">М2 · Оценка проектов</span></div>
        <div className="criteria-list"><div className="criteria-row criteria-head"><span>Критерий</span><span>Вес</span><span>Обратная шкала</span><span /></div>
          {criteriaDraft.map((c, i) => <div className="criteria-row" key={i}><div className="criteria-name"><input aria-label="Название критерия" value={c.name} onChange={e => setCriteriaDraft(criteriaDraft.map((it, index) => index === i ? { ...it, name: e.target.value } : it))} /><input className="description-input" aria-label="Описание критерия" value={c.description} placeholder="Краткое описание" onChange={e => setCriteriaDraft(criteriaDraft.map((it, index) => index === i ? { ...it, description: e.target.value } : it))} /></div><input aria-label="Вес критерия" type="number" min="0" max="1" step="0.0001" value={c.weight} onChange={e => setCriteriaDraft(criteriaDraft.map((it, index) => index === i ? { ...it, weight: e.target.value } : it))} /><input aria-label="Обратное направление" type="checkbox" checked={c.reverse_direction} onChange={e => setCriteriaDraft(criteriaDraft.map((it, index) => index === i ? { ...it, reverse_direction: e.target.checked } : it))} /><button className="icon-button" aria-label="Удалить критерий" onClick={() => setCriteriaDraft(criteriaDraft.filter((_, index) => index !== i))}>×</button></div>)}
        </div>
        <div className="toolbar"><button className="secondary" onClick={() => setCriteriaDraft([...criteriaDraft, { name: '', description: '', weight: '0.0000', reverse_direction: false }])}>+ Критерий</button><span className="muted tiny">Текущая сумма: {criteriaDraft.reduce((sum, c) => sum + (Number(c.weight) || 0), 0).toFixed(4)}</span><button disabled={busy} onClick={saveCriteria}>Сохранить шаблон</button></div>
      </section>}

      {view === 'assessment' && user.role === 'EXPERT' && <section className="panel"><div className="panel-heading"><div><h2>Оценка заявки</h2><p>Каждый назначенный эксперт выставляет целую оценку от 1 до 5 по каждому активному критерию.</p></div><span className="tag">М2 · Экспертиза</span></div>
        <form className="stack" onSubmit={saveAssessment}>
          <label>Заявка<select value={assessmentProject} onChange={e => setAssessmentProject(e.target.value)} required><option value="">Выберите заявку…</option>{requests.filter(r => r.status === 'UNDER_EVALUATION').map(p => <option key={p.id} value={p.id}>{p.code} — {p.title}</option>)}</select></label>
          {criteria.map(c => <div className="assessment-row" key={c.id}><div><strong>{c.name}</strong><p>{c.description}</p><small>Вес: {c.weight}{c.reverse_direction ? ' · обратная шкала' : ''}</small></div><label className="score-field">Балл<select value={scores[c.id] ?? ''} onChange={e => setScores({ ...scores, [c.id]: e.target.value })} required><option value="">—</option>{[1, 2, 3, 4, 5].map(v => <option value={v} key={v}>{v}</option>)}</select></label><label>Комментарий<input value={comments[c.id] ?? ''} onChange={e => setComments({ ...comments, [c.id]: e.target.value })} maxLength={1000} /></label></div>)}
          <div className="form-footer"><span className="muted tiny">Рейтинг появится после того, как все эксперты завершат оценку.</span><button disabled={busy || !criteria.length}>Сохранить оценки</button></div>
        </form>
      </section>}

      {view === 'scenarios' && isManager && <>
        <section className="panel"><div className="panel-heading"><div><h2>Новый сценарий</h2><p>Лимит бюджета задаётся в млн рублей.</p></div><span className="tag">М3 · Моделирование</span></div>
          <form onSubmit={createScenario} className="form-grid scenario-form"><label>Название<input value={scenarioForm.name} onChange={e => setScenarioForm({ ...scenarioForm, name: e.target.value })} required maxLength={160} /></label><label>Лимит, млн руб.<input type="number" min="0.01" max="100000" step="0.01" value={scenarioForm.budget_limit} onChange={e => setScenarioForm({ ...scenarioForm, budget_limit: e.target.value })} required /></label><div className="form-footer"><button disabled={busy}>Создать сценарий</button></div></form>
        </section>
        {scenarios.map(s => <section className="panel" key={s.scenario.id}><div className="panel-heading"><div><h2>{s.scenario.name}</h2><p>Сценарий №{s.scenario.id} · лимит {s.scenario.budget_limit} млн руб.</p></div><span className={statusClass(s.scenario.status)}>{s.scenario.status_label}</span></div>
          <div className="scenario-stats"><div><span>Использовано</span><strong>{s.totals.used}</strong></div><div><span>Остаток</span><strong>{s.totals.remainder}</strong></div><div><span>Выгода</span><strong>{s.totals.total_benefit}</strong></div><div><span>Заполнение</span><strong>{s.totals.percentage}%</strong></div></div>
          <div className={`budget-track ${!s.totals.within_budget ? 'over' : ''}`}><span style={{ width: `${Math.min(100, Math.max(0, Number(s.totals.percentage)))}%` }} /></div>
          <div className="table-wrap"><table><thead><tr><th>Включить</th><th>Ранг</th><th>Проект</th><th>Рейтинг</th><th>Бюджет</th><th>Выгода</th><th>Статус</th></tr></thead><tbody>{s.items.map(item => <tr key={item.id}><td><input type="checkbox" checked={item.included} disabled={s.scenario.status !== 'DRAFT'} onChange={e => toggleItem(s.scenario.id, item, e.target.checked)} /></td><td>{fmt(item.rank_order)}</td><td><strong>{item.code}</strong><span className="table-subtitle">{item.title}</span></td><td>{fmt(item.rating)}</td><td>{item.budget}</td><td>{item.expected_benefit}</td><td>{item.status}</td></tr>)}{!s.items.length && <tr><td colSpan={7} className="empty">Сначала запустите автоподбор, чтобы добавить оценённые проекты.</td></tr>}</tbody></table></div>
          {s.scenario.status === 'DRAFT' && <div className="toolbar"><button className="secondary" onClick={() => autoSelect(s.scenario.id)}>Автоматически подобрать</button><button disabled={busy || !s.items.some(i => i.included) || !s.totals.within_budget} onClick={() => submitScenario(s.scenario.id)}>Передать комитету</button></div>}
          {!s.totals.within_budget && <p className="warning-text">Лимит превышен: передача в комитет заблокирована.</p>}
        </section>)}
        {!scenarios.length && <section className="panel empty">Сценариев пока нет — создайте первый выше.</section>}
      </>}

      {view === 'committee' && user.role === 'COMMITTEE_MEMBER' && <section className="panel"><div className="panel-heading"><div><h2>Голосование инвестиционного комитета</h2><p>«За», «Против» или «Воздержался». Воздержавшиеся входят в знаменатель большинства.</p></div><span className="tag">М4 · Утверждение</span></div>
        {scenarios.filter(s => s.scenario.status === 'SUBMITTED').map(s => <div className="committee-scenario" key={s.scenario.id}><h3>{s.scenario.name} <small>· лимит {s.scenario.budget_limit}</small></h3>{s.items.filter(i => i.included).map(item => <div className="vote-row" key={item.id}><div><strong>{item.code} — {item.title}</strong><p>Бюджет {item.budget} · рейтинг {fmt(item.rating)}</p></div><div className="vote-buttons"><button className="vote-for" onClick={() => vote(s.scenario.id, item.id, 'FOR')}>За</button><button className="vote-against" onClick={() => vote(s.scenario.id, item.id, 'AGAINST')}>Против</button><button className="secondary" onClick={() => vote(s.scenario.id, item.id, 'ABSTAIN')}>Воздержаться</button></div></div>)}</div>)}
        {!scenarios.some(s => s.scenario.status === 'SUBMITTED') && <p className="empty">Нет сценариев, ожидающих голосования.</p>}
      </section>}

      {view === 'protocols' && <section className="panel"><div className="panel-heading"><div><h2>Протоколы инвестиционного комитета</h2><p>Решения фиксируются после достижения кворума.</p></div><span className="tag">М4 · Протокол</span></div>
        {protocols.map(p => <div className="protocol-card" key={p.scenario.id}><h3>{p.scenario.name} <span className="muted">· {p.scenario.finalized_at ? new Date(p.scenario.finalized_at).toLocaleString('ru-RU') : 'решение зафиксировано'}</span></h3><div className="table-wrap"><table><thead><tr><th>Проект</th><th>За</th><th>Против</th><th>Воздержались</th><th>Итог</th></tr></thead><tbody>{p.projects.map(row => <tr key={row.id}><td>{row.code} — {row.title}</td><td>{row.for}</td><td>{row.against}</td><td>{row.abstain}</td><td><span className={statusClass(row.status)}>{row.status}</span></td></tr>)}</tbody></table></div></div>)}
        {!protocols.length && <p className="empty">Зафиксированных протоколов пока нет.</p>}
        {isManager && scenarios.filter(s => s.scenario.status === 'SUBMITTED').map(s => <div className="toolbar" key={s.scenario.id}><span>Сценарий «{s.scenario.name}» — готов к фиксации при наличии кворума по каждой заявке.</span><button onClick={() => finalize(s.scenario.id)} disabled={busy}>Зафиксировать решения</button></div>)}
      </section>}

      {view === 'audit' && user.role === 'ADMIN' && <section className="panel"><div className="panel-heading"><div><h2>Журнал аудита и интеграций</h2><p>Записи доступны только для чтения; журнал отражает действия пользователей и события SMTP.</p></div><span className="tag">М6 · Аудит</span></div>
        <div className="filter-bar"><label>Пользователь<input value={auditFilters.username} onChange={e => setAuditFilters({ ...auditFilters, username: e.target.value })} placeholder="например, manager" /></label><label>Тип события<input value={auditFilters.event_type} onChange={e => setAuditFilters({ ...auditFilters, event_type: e.target.value })} placeholder="REQUEST_SUBMITTED" /></label><button className="secondary" onClick={loadFilteredLogs}>Фильтровать</button></div>
        <div className="table-wrap"><table><thead><tr><th>Время UTC</th><th>Пользователь</th><th>Событие</th><th>Объект</th><th>Статус</th><th>Детали</th></tr></thead><tbody>{logs.map(log => <tr key={log.id}><td>{new Date(log.created_at).toISOString().replace('T', ' ').replace('Z', '')}</td><td>{log.username ?? 'system'}</td><td><code>{log.event_type}</code></td><td>{log.object_type} #{log.object_id}</td><td><span className={statusClass(log.status)}>{log.status}</span></td><td><details><summary>JSON</summary><pre>{JSON.stringify({ before: log.before_data, after: log.after_data, message: log.message }, null, 2)}</pre></details></td></tr>)}{!logs.length && <tr><td colSpan={6} className="empty">События не найдены.</td></tr>}</tbody></table></div>
      </section>}
      <footer className="page-footer"><span>PPM MVP · Верификация информационных систем</span><span>Детерминированные расчёты · RBAC · аудит</span></footer>
    </main>
  </div>;
}
