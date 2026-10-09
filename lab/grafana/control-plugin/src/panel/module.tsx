import * as React from 'react';
import { PanelPlugin, PanelProps } from '@grafana/data';
import { config, getBackendSrv, getTemplateSrv } from '@grafana/runtime';

type State = {
  mode: 'auto' | 'manual'; profile: string; confirmed_path: string | null;
  observing_native_qbr: boolean; network_available: boolean;
  pending: { id: string; source: string; target: string; reason: string; expires_at: number } | null;
  apply_inflight: { target: string } | null;
};
const proxy = '/api/plugin-proxy/aisdwan-controls-app';
const buttonStyle: React.CSSProperties = {
  border: '1px solid #5794f2', borderRadius: 4, padding: '6px 12px',
  background: '#1f2c46', color: '#fff', cursor: 'pointer', fontSize: 13,
};

export function Controls({ data, width, height }: PanelProps) {
  const [state, setState] = React.useState<State | null>(null);
  const [profile, setProfile] = React.useState('voice');
  const [error, setError] = React.useState('');
  const [message, setMessage] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [now, setNow] = React.useState(Date.now());
  const [readAt, setReadAt] = React.useState(0);
  const mounted = React.useRef(false);
  const writing = React.useRef(false);
  const revision = React.useRef(0);

  React.useEffect(() => {
    mounted.current = true;
    let reading = false;
    async function poll() {
      if (reading || writing.current) { return; }
      reading = true;
      const startedAtRevision = revision.current;
      try {
        const next = await getBackendSrv().get<State>(proxy + '/state');
        if (mounted.current && !writing.current && revision.current === startedAtRevision) {
          setState(next); setReadAt(Date.now()); setError('');
        }
      } catch (err) {
        if (mounted.current && revision.current === startedAtRevision) { setError(detail(err)); }
      } finally { reading = false; }
    }
    void poll();
    const timer = setInterval(poll, 5000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { mounted.current = false; clearInterval(timer); clearInterval(clock); };
  }, []);
  React.useEffect(() => { if (state?.profile) { setProfile(state.profile); } }, [state?.profile]);

  const selected = getTemplateSrv().replace('$stream');
  const liveField = data.series.flatMap(frame => frame.fields).find(field => field.name === 'live_stream');
  const liveStream = liveField?.values[0];
  const live = selected === '__latest_live__' || (typeof liveStream === 'string' && selected === liveStream);
  const admin = config.bootData.user.orgRole === 'Admin';
  const fresh = readAt > 0 && now - readAt < 10000 && !error;
  const enabled = !!state && admin && live && fresh && !busy && !state.observing_native_qbr && !state.apply_inflight;
  const pending = state?.pending;
  const remaining = pending ? Math.max(0, Math.ceil(pending.expires_at - now / 1000)) : 0;
  const canReview = enabled && state?.mode === 'manual' && !!pending && remaining > 0;

  async function send(path: string, body: object, success: string) {
    if (!enabled || writing.current) { return; }
    writing.current = true; revision.current += 1; setBusy(true); setMessage('');
    try {
      const next = await getBackendSrv().post<State>(proxy + path, body);
      if (mounted.current) {
        setState(next); setReadAt(Date.now()); setError('');
        setMessage(next.apply_inflight ? 'Approval submitted; waiting for controller verification.' : success);
      }
    } catch (err) {
      if (mounted.current) { setError(detail(err)); }
    } finally {
      writing.current = false;
      if (mounted.current) { setBusy(false); }
    }
  }
  function review(action: 'approve' | 'reject') {
    if (!canReview || !pending) { return; }
    void send('/recommendations/' + encodeURIComponent(pending.id), { action },
      action === 'approve' ? 'Recommendation approved.' : 'Recommendation rejected.');
  }
  function button(label: string, disabled: boolean, click: () => void, active = false) {
    return <button type="button" disabled={disabled} onClick={click}
      aria-pressed={active} style={{ ...buttonStyle, opacity: disabled ? 0.45 : 1,
        cursor: disabled ? 'default' : 'pointer', background: active ? '#245a40' : '#1f2c46' }}>{label}</button>;
  }
  return <div style={{ width, height, overflow: 'auto', padding: '4px 8px', fontSize: 13 }}>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
      <strong>Control mode</strong>
      {button('Auto', !enabled || state?.mode === 'auto', () => void send('/configuration', { mode: 'auto' }, 'Auto mode enabled.'), state?.mode === 'auto')}
      {button('Manual', !enabled || state?.mode === 'manual', () => void send('/configuration', { mode: 'manual' }, 'Manual mode enabled.'), state?.mode === 'manual')}
      <span>{state?.mode === 'auto' ? 'Automatic decisions; no approval required.' : 'Manual decisions require approval.'}</span>
    </div>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
      <label htmlFor="sdwan-profile">Application profile</label>
      <select id="sdwan-profile" aria-label="Application profile" value={profile} disabled={!enabled}
        onChange={event => setProfile(event.target.value)} style={{ ...buttonStyle, padding: 6 }}>
        <option value="voice">Voice</option><option value="interactive">Interactive</option><option value="bulk">Bulk</option>
      </select>
      {button('Apply profile', !enabled || profile === state?.profile,
        () => void send('/configuration', { profile }, 'Application profile updated.'))}
      <span>Configured priority: {state?.confirmed_path?.toUpperCase() || 'Unknown'}</span>
    </div>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
      <strong>Pending recommendation</strong>
      <span>{pending ? `${pending.source?.toUpperCase() || 'Current'} → ${pending.target.toUpperCase()} (${remaining}s)` : 'None'}</span>
      {button('Approve', !canReview, () => review('approve'))}
      {button('Reject', !canReview, () => review('reject'))}
    </div>
    {pending && <div>{pending.reason}</div>}
    {state?.apply_inflight && <div role="status">Applying {state.apply_inflight.target.toUpperCase()}; waiting for verified readback.</div>}
    {!live && <div role="status">Archived stream: select Latest live to use controls.</div>}
    {!admin && <div role="status">Grafana Admin access is required to change settings or approve recommendations.</div>}
    {state?.observing_native_qbr && <div role="status">Native QBR observation is read-only.</div>}
    {!state && !error && <div role="status">Loading current decision service…</div>}
    {state && !fresh && !error && <div role="status">Refreshing decision service; controls paused.</div>}
    {error && <div role="alert" style={{ color: '#ff9830' }}>{error}</div>}
    {message && <div role="status">{message}</div>}
  </div>;
}
function detail(error: unknown): string {
  const err = error as { data?: { detail?: string; message?: string }; status?: number };
  return err?.data?.detail || err?.data?.message || (err?.status === 401
    ? 'Sign in to Grafana to use controls.' : 'Control request failed. Refresh state and try again.');
}
export const plugin = new PanelPlugin(Controls);
