define(["require", "exports", "react", "@grafana/data", "@grafana/runtime"], function (require, exports, React, data_1, runtime_1) {
    "use strict";
    Object.defineProperty(exports, "__esModule", { value: true });
    exports.plugin = void 0;
    exports.Controls = Controls;
    const proxy = '/api/plugin-proxy/aisdwan-controls-app';
    const buttonStyle = {
        border: '1px solid #5794f2', borderRadius: 4, padding: '6px 12px',
        background: '#1f2c46', color: '#fff', cursor: 'pointer', fontSize: 13,
    };
    function Controls({ data, width, height }) {
        const [state, setState] = React.useState(null);
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
                if (reading || writing.current) {
                    return;
                }
                reading = true;
                const startedAtRevision = revision.current;
                try {
                    const next = await (0, runtime_1.getBackendSrv)().get(proxy + '/state');
                    if (mounted.current && !writing.current && revision.current === startedAtRevision) {
                        setState(next);
                        setReadAt(Date.now());
                        setError('');
                    }
                }
                catch (err) {
                    if (mounted.current && revision.current === startedAtRevision) {
                        setError(detail(err));
                    }
                }
                finally {
                    reading = false;
                }
            }
            void poll();
            const timer = setInterval(poll, 5000);
            const clock = setInterval(() => setNow(Date.now()), 1000);
            return () => { mounted.current = false; clearInterval(timer); clearInterval(clock); };
        }, []);
        React.useEffect(() => { if (state?.profile) {
            setProfile(state.profile);
        } }, [state?.profile]);
        const selected = (0, runtime_1.getTemplateSrv)().replace('$stream');
        const liveField = data.series.flatMap(frame => frame.fields).find(field => field.name === 'live_stream');
        const liveStream = liveField?.values[0];
        const live = selected === '__latest_live__' || (typeof liveStream === 'string' && selected === liveStream);
        const admin = runtime_1.config.bootData.user.orgRole === 'Admin';
        const fresh = readAt > 0 && now - readAt < 10000 && !error;
        const enabled = !!state && admin && live && fresh && !busy && !state.observing_native_qbr && !state.apply_inflight;
        const pending = state?.pending;
        const remaining = pending ? Math.max(0, Math.ceil(pending.expires_at - now / 1000)) : 0;
        const canReview = enabled && state?.mode === 'manual' && !!pending && remaining > 0;
        async function send(path, body, success) {
            if (!enabled || writing.current) {
                return;
            }
            writing.current = true;
            revision.current += 1;
            setBusy(true);
            setMessage('');
            try {
                const next = await (0, runtime_1.getBackendSrv)().post(proxy + path, body);
                if (mounted.current) {
                    setState(next);
                    setReadAt(Date.now());
                    setError('');
                    setMessage(next.apply_inflight ? 'Approval submitted; waiting for controller verification.' : success);
                }
            }
            catch (err) {
                if (mounted.current) {
                    setError(detail(err));
                }
            }
            finally {
                writing.current = false;
                if (mounted.current) {
                    setBusy(false);
                }
            }
        }
        function review(action) {
            if (!canReview || !pending) {
                return;
            }
            void send('/recommendations/' + encodeURIComponent(pending.id), { action }, action === 'approve' ? 'Recommendation approved.' : 'Recommendation rejected.');
        }
        function button(label, disabled, click, active = false) {
            return React.createElement("button", { type: "button", disabled: disabled, onClick: click, "aria-pressed": active, style: { ...buttonStyle, opacity: disabled ? 0.45 : 1,
                    cursor: disabled ? 'default' : 'pointer', background: active ? '#245a40' : '#1f2c46' } }, label);
        }
        return React.createElement("div", { style: { width, height, overflow: 'auto', padding: '4px 8px', fontSize: 13 } },
            React.createElement("div", { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 } },
                React.createElement("strong", null, "Control mode"),
                button('Auto', !enabled || state?.mode === 'auto', () => void send('/configuration', { mode: 'auto' }, 'Auto mode enabled.'), state?.mode === 'auto'),
                button('Manual', !enabled || state?.mode === 'manual', () => void send('/configuration', { mode: 'manual' }, 'Manual mode enabled.'), state?.mode === 'manual'),
                React.createElement("span", null, state?.mode === 'auto' ? 'Automatic decisions; no approval required.' : 'Manual decisions require approval.')),
            React.createElement("div", { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 } },
                React.createElement("label", { htmlFor: "sdwan-profile" }, "Application profile"),
                React.createElement("select", { id: "sdwan-profile", "aria-label": "Application profile", value: profile, disabled: !enabled, onChange: event => setProfile(event.target.value), style: { ...buttonStyle, padding: 6 } },
                    React.createElement("option", { value: "voice" }, "Voice"),
                    React.createElement("option", { value: "interactive" }, "Interactive"),
                    React.createElement("option", { value: "bulk" }, "Bulk")),
                button('Apply profile', !enabled || profile === state?.profile, () => void send('/configuration', { profile }, 'Application profile updated.')),
                React.createElement("span", null,
                    "Configured priority: ",
                    state?.confirmed_path?.toUpperCase() || 'Unknown')),
            React.createElement("div", { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 } },
                React.createElement("strong", null, "Pending recommendation"),
                React.createElement("span", null, pending ? `${pending.source?.toUpperCase() || 'Current'} → ${pending.target.toUpperCase()} (${remaining}s)` : 'None'),
                button('Approve', !canReview, () => review('approve')),
                button('Reject', !canReview, () => review('reject'))),
            pending && React.createElement("div", null, pending.reason),
            state?.apply_inflight && React.createElement("div", { role: "status" },
                "Applying ",
                state.apply_inflight.target.toUpperCase(),
                "; waiting for verified readback."),
            !live && React.createElement("div", { role: "status" }, "Archived stream: select Latest live to use controls."),
            !admin && React.createElement("div", { role: "status" }, "Grafana Admin access is required to change settings or approve recommendations."),
            state?.observing_native_qbr && React.createElement("div", { role: "status" }, "Native QBR observation is read-only."),
            !state && !error && React.createElement("div", { role: "status" }, "Loading current decision service\u2026"),
            state && !fresh && !error && React.createElement("div", { role: "status" }, "Refreshing decision service; controls paused."),
            error && React.createElement("div", { role: "alert", style: { color: '#ff9830' } }, error),
            message && React.createElement("div", { role: "status" }, message));
    }
    function detail(error) {
        const err = error;
        return err?.data?.detail || err?.data?.message || (err?.status === 401
            ? 'Sign in to Grafana to use controls.' : 'Control request failed. Refresh state and try again.');
    }
    exports.plugin = new data_1.PanelPlugin(Controls);
});
