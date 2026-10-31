from flask import render_template_string


def latest_patient_decision(events):
    for event in events:
        if event.get("note") == "triage decision":
            if event.get("level_ai") and event.get("level_final"):
                return {
                    "kind": "triage",
                    "level_ai": event.get("level_ai"),
                    "level_memory": event.get("level_memory"),
                    "level_final": event.get("level_final"),
                    "created_at": event.get("created_at"),
                }
            continue
        if not event.get("ok"):
            continue
        if event.get("event_type") == "resolve":
            return {
                "kind": "resolved",
                "level_memory": event.get("level_memory"),
                "created_at": event.get("created_at"),
            }
        if event.get("event_type") == "correct":
            return {
                "kind": "corrected",
                "level_memory": event.get("level_memory"),
                "created_at": event.get("created_at"),
            }
        if event.get("event_type") == "forget":
            return {
                "kind": "forgotten",
                "created_at": event.get("created_at"),
            }
    return None


MEKO_PANEL_HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Meko Memory — MatriSetu</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Syne:wght@600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --deep:#054235; --mid:#0a6b52; --green:#10b981;
            --pale:#d1fae5; --bg:#f0faf5; --ink:#111827;
            --muted:#6b7280; --border:#e5e7eb; --red:#dc2626;
            --amber:#b45309; --shadow:0 4px 16px rgba(5,66,53,.08);
        }
        * { box-sizing:border-box; }
        body { margin:0; background:var(--bg); color:var(--ink); font-family:'DM Sans',sans-serif; }
        header { padding:22px max(18px,calc((100vw - 1180px)/2)); color:white;
            background:linear-gradient(135deg,var(--deep),var(--mid)); }
        .topline { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
        h1 { margin:0; font:800 24px 'Syne',sans-serif; }
        a { color:inherit; }
        .back { text-decoration:none; font-weight:600; }
        .status { border-radius:999px; padding:7px 13px; font-size:12px; font-weight:700;
            background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.3); }
        .status.off { background:rgba(239,68,68,.2); }
        .sub { margin:8px 0 0; font-size:13px; opacity:.82; }
        main { max-width:1180px; margin:0 auto; padding:18px; }
        .summary { display:grid; grid-template-columns:repeat(6,minmax(100px,1fr)); gap:10px; margin-bottom:16px; }
        .metric,.patient { background:white; border:1px solid var(--pale); border-radius:15px; box-shadow:var(--shadow); }
        .metric { padding:14px; text-align:center; }
        .metric strong { display:block; font:700 23px 'Syne',sans-serif; color:var(--deep); }
        .metric span { color:var(--muted); font-size:10px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
        .last-call { color:var(--muted); font-size:12px; margin:0 0 18px; }
        .notice { display:none; border-radius:10px; padding:12px 14px; margin-bottom:14px; font-size:13px; }
        .notice.off { display:block; color:#991b1b; background:#fef2f2; border:1px solid #fecaca; }
        .patient-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr)); gap:14px; }
        .patient { overflow:hidden; }
        .patient-head { padding:16px 18px; border-bottom:1px solid #edf2ef; display:flex; justify-content:space-between; gap:8px; }
        .patient-head h2 { margin:0; font:700 16px 'Syne',sans-serif; }
        .patient-head small { color:var(--muted); }
        .patient-body { padding:15px 18px; }
        .section { margin:0 0 15px; }
        .section:last-child { margin-bottom:0; }
        .section h3 { margin:0 0 7px; color:var(--mid); font-size:11px; letter-spacing:.08em; }
        .muted { color:var(--muted); font-size:12px; }
        .fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; font-size:12px; }
        .fields div { padding:7px 9px; background:#f7fbf8; border-radius:8px; overflow-wrap:anywhere; }
        .excerpt { margin:8px 0 0; padding:9px; white-space:pre-wrap; overflow-wrap:anywhere;
            background:#f7fbf8; border-left:3px solid var(--green); border-radius:6px; font:11px/1.5 monospace; }
        .decision { padding:10px; border-radius:9px; background:#f0faf5; font-size:13px; }
        .decision.raise { background:#fef2f2; color:#991b1b; }
        .record-list,.event-list { list-style:none; padding:0; margin:0; }
        .record-list li,.event-list li { padding:7px 0; border-top:1px solid #eef2ef; font-size:11px; overflow-wrap:anywhere; }
        .event-list li { display:flex; justify-content:space-between; gap:8px; }
        .event-ok { color:#15803d; font-weight:700; }
        .event-fail { color:var(--red); font-weight:700; }
        .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
        button { border:0; border-radius:8px; padding:9px 13px; cursor:pointer; font:600 12px 'DM Sans',sans-serif; }
        button:disabled { opacity:.5; cursor:not-allowed; }
        .correct-button { background:var(--deep); color:white; }
        .forget-button { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
        .correction-form { display:none; margin-top:10px; padding:10px; border:1px solid var(--border); border-radius:9px; }
        .correction-form.open { display:flex; gap:8px; flex-wrap:wrap; }
        select { border:1px solid var(--border); border-radius:7px; padding:8px; background:white; }
        .seeded { display:inline-block; margin-left:5px; border-radius:999px; padding:2px 6px;
            color:#6d28d9; background:#f5f3ff; font-size:9px; font-weight:700; }
        .toast { min-height:18px; margin-top:8px; font-size:12px; color:var(--mid); }
        @media(max-width:680px) {
            main { padding:12px; }
            .summary { grid-template-columns:repeat(2,minmax(0,1fr)); }
            .patient-grid { grid-template-columns:1fr; }
            h1 { font-size:20px; }
        }
    </style>
</head>
<body>
<header>
    <div class="topline">
        <a class="back" href="/dashboard/{{ asha_id }}">← ASHA dashboard</a>
        <h1>Meko Memory</h1>
        <span id="meko-status" class="status {{ 'on' if meko_enabled else 'off' }}">
            {{ 'MEKO ON' if meko_enabled else 'MEKO OFF' }}
        </span>
    </div>
    <p class="sub">See what Meko recalled, what MatriSetu stored, and how the triage decision was made.</p>
</header>
<main>
    <div id="off-notice" class="notice {{ 'off' if not meko_enabled else '' }}">
        Meko is OFF. Patient care continues without Meko calls or memory.
    </div>
    <section class="summary" aria-label="Meko activity">
        <div class="metric"><strong id="count-recalls">{{ summary.get('recalls', 0) }}</strong><span>Recalls</span></div>
        <div class="metric"><strong id="count-stores">{{ summary.get('stores', 0) }}</strong><span>Stores</span></div>
        <div class="metric"><strong id="count-corrections">{{ summary.get('corrections', 0) }}</strong><span>Corrections</span></div>
        <div class="metric"><strong id="count-forgets">{{ summary.get('forgets', 0) }}</strong><span>Forgets</span></div>
        <div class="metric"><strong id="count-failures">{{ summary.get('failures', 0) }}</strong><span>Failures</span></div>
        <div class="metric"><strong id="patient-count">{{ patients|length }}</strong><span>Patients</span></div>
    </section>
    <p class="last-call">Last successful Meko call: <span id="last-success">
        {{ summary.get('last_successful_at') or 'None yet' }}
    </span></p>
    <section id="patient-grid" class="patient-grid" aria-live="polite">
        {% for patient in patients %}
        <article class="patient" data-phone="{{ patient.phone }}">
            <div class="patient-head">
                <h2>{{ patient.name or 'Patient' }}</h2>
                <small>Week {{ patient.week if patient.week is not none else '—' }}</small>
            </div>
            <div class="patient-body">
                <section class="section">
                    <h3>RECALLED</h3>
                    <div class="recall-content muted">
                        {{ 'Meko is OFF' if not meko_enabled else 'Waiting for live Meko result…' }}
                    </div>
                </section>
                <section class="section">
                    <h3>STORED</h3>
                    <ul class="record-list">
                        {% for event in patient.stored %}
                        <li>
                            {{ event.created_at or 'Time unavailable' }} · {{ event.event_type }}
                            · {{ event.level_final or event.level_memory or '—' }}
                            · ID {{ event.memory_id or 'pending' }}
                            {% if 'seeded' in (event.note or '')|lower %}<span class="seeded">seeded</span>{% endif %}
                        </li>
                        {% else %}
                        <li class="muted">No locally logged memory writes yet.</li>
                        {% endfor %}
                    </ul>
                </section>
                <section class="section">
                    <h3>DECISION</h3>
                    <div class="decision-content muted">
                        {% if patient.decision %}
                        {% if patient.decision.kind == 'resolved' %}
                        Recovery confirmed. Meko memory is {{ patient.decision.level_memory or 'GREEN' }}/resolved; the previous escalation is resolved.
                        {% elif patient.decision.kind == 'corrected' %}
                        Meko memory was corrected. Current stored level: {{ patient.decision.level_memory or '—' }}.
                        {% elif patient.decision.kind == 'forgotten' %}
                        Meko memory was forgotten and will not be used for future triage recalls.
                        {% else %}
                        AI said {{ patient.decision.level_ai or '—' }},
                        Meko memory said {{ patient.decision.level_memory or 'nothing' }},
                        final {{ patient.decision.level_final or '—' }}.
                        {% endif %}
                        {% else %}No triage decision recorded yet.{% endif %}
                    </div>
                </section>
                <section class="section">
                    <h3>LAST 20 EVENTS</h3>
                    <ul class="event-list">
                        {% for event in patient.events %}
                        <li>
                            <span>{{ event.created_at or 'Time unavailable' }} · {{ event.event_type }} · {{ event.latency_ms or 0 }} ms</span>
                            <span class="{{ 'event-ok' if event.ok else 'event-fail' }}">{{ 'OK' if event.ok else 'FAILED' }}</span>
                        </li>
                        {% else %}
                        <li class="muted">No Meko events logged yet.</li>
                        {% endfor %}
                    </ul>
                </section>
                <div class="actions">
                    <button class="correct-button" type="button" {{ 'disabled' if not meko_enabled else '' }}>Correct memory</button>
                    <button class="forget-button" type="button" {{ 'disabled' if not meko_enabled else '' }}>Forget memory</button>
                </div>
                <form class="correction-form">
                    <label>Level
                        <select name="level">
                            <option>GREEN</option><option>AMBER</option><option>RED</option>
                        </select>
                    </label>
                    <label>Status
                        <select name="status">
                            <option value="active">Active</option><option value="resolved">Resolved</option>
                        </select>
                    </label>
                    <button class="correct-button" type="submit" {{ 'disabled' if not meko_enabled else '' }}>Save correction</button>
                </form>
                <div class="toast" role="status"></div>
            </div>
        </article>
        {% else %}
        <div class="patient">
            <div class="patient-body muted">No registered patients are assigned to this ASHA worker.</div>
        </div>
        {% endfor %}
    </section>
</main>
<script>
(() => {
    const enabled = {{ 'true' if meko_enabled else 'false' }};
    const grid = document.getElementById('patient-grid');
    const pageUrl = window.location.pathname;
    const rank = {GREEN: 0, AMBER: 1, RED: 2};
    const byId = (id) => document.getElementById(id);
    const setText = (node, value) => { node.textContent = value == null ? '' : String(value); };
    const addLine = (list, text, seeded) => {
        const li = document.createElement('li');
        setText(li, text);
        if (seeded) {
            const badge = document.createElement('span');
            badge.className = 'seeded';
            setText(badge, 'seeded');
            li.appendChild(badge);
        }
        list.appendChild(li);
    };
    const formatDate = (value) => {
        if (!value) return 'Time unavailable';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
    };
    const ageText = (seconds) => {
        const age = Math.max(0, Number(seconds) || 0);
        if (age < 60) return `${age} seconds`;
        if (age < 3600) return `${Math.floor(age / 60)} minutes`;
        return `${Math.floor(age / 3600)} hours`;
    };
    const makePatient = (patient) => {
        const card = document.createElement('article');
        card.className = 'patient';
        card.dataset.phone = patient.phone;
        const head = document.createElement('div');
        head.className = 'patient-head';
        const title = document.createElement('h2');
        setText(title, patient.name || 'Patient');
        const week = document.createElement('small');
        setText(week, `Week ${patient.week == null ? '—' : patient.week}`);
        head.append(title, week);
        const body = document.createElement('div');
        body.className = 'patient-body';

        const recalled = document.createElement('section');
        recalled.className = 'section';
        const recalledTitle = document.createElement('h3');
        setText(recalledTitle, 'RECALLED');
        const recallContent = document.createElement('div');
        recallContent.className = 'recall-content muted';
        setText(recallContent, enabled ? 'Waiting for live Meko result…' : 'Meko is OFF');
        recalled.append(recalledTitle, recallContent);

        const stored = document.createElement('section');
        stored.className = 'section';
        const storedTitle = document.createElement('h3');
        setText(storedTitle, 'STORED');
        const storedList = document.createElement('ul');
        storedList.className = 'record-list';
        stored.append(storedTitle, storedList);

        const decision = document.createElement('section');
        decision.className = 'section';
        const decisionTitle = document.createElement('h3');
        setText(decisionTitle, 'DECISION');
        const decisionContent = document.createElement('div');
        decisionContent.className = 'decision-content muted';
        setText(decisionContent, 'No triage decision recorded yet.');
        decision.append(decisionTitle, decisionContent);

        const events = document.createElement('section');
        events.className = 'section';
        const eventsTitle = document.createElement('h3');
        setText(eventsTitle, 'LAST 20 EVENTS');
        const eventList = document.createElement('ul');
        eventList.className = 'event-list';
        events.append(eventsTitle, eventList);

        const actions = document.createElement('div');
        actions.className = 'actions';
        const correct = document.createElement('button');
        correct.type = 'button';
        correct.className = 'correct-button';
        correct.disabled = !enabled;
        setText(correct, 'Correct memory');
        const forget = document.createElement('button');
        forget.type = 'button';
        forget.className = 'forget-button';
        forget.disabled = !enabled;
        setText(forget, 'Forget memory');
        actions.append(correct, forget);

        const form = document.createElement('form');
        form.className = 'correction-form';
        for (const [labelText, name, values] of [
            ['Level', 'level', ['GREEN', 'AMBER', 'RED']],
            ['Status', 'status', ['active', 'resolved']],
        ]) {
            const label = document.createElement('label');
            setText(label, labelText + ' ');
            const select = document.createElement('select');
            select.name = name;
            values.forEach((value) => {
                const option = document.createElement('option');
                option.value = value;
                setText(option, value);
                select.appendChild(option);
            });
            label.appendChild(select);
            form.appendChild(label);
        }
        const submit = document.createElement('button');
        submit.type = 'submit';
        submit.className = 'correct-button';
        submit.disabled = !enabled;
        setText(submit, 'Save correction');
        form.appendChild(submit);
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.setAttribute('role', 'status');
        body.append(recalled, stored, decision, events, actions, form, toast);
        card.append(head, body);
        return card;
    };
    const renderPatient = (patient) => {
        let card = Array.from(grid.children).find((item) => item.dataset.phone === patient.phone);
        if (!card) {
            card = makePatient(patient);
            grid.appendChild(card);
        }
        const memory = patient.memory || {state: enabled ? 'loading' : 'off'};
        const recalled = card.querySelector('.recall-content');
        recalled.replaceChildren();
        if (memory.state === 'off') {
            setText(recalled, 'Meko is OFF');
        } else if (memory.state === 'loading') {
            setText(recalled, 'Waiting for live Meko result…');
        } else if (memory.state === 'unavailable') {
            setText(recalled, 'Meko unavailable. Existing MatriSetu care continues.');
        } else if (!memory.record) {
            setText(recalled, 'Meko returned no matching triage record.');
        } else {
            const record = memory.record;
            const correctionForm = card.querySelector('.correction-form');
            if (correctionForm) {
                correctionForm.elements.level.value = record.level || 'GREEN';
                correctionForm.elements.status.value = record.status || 'active';
            }
            const fields = document.createElement('div');
            fields.className = 'fields';
            for (const [label, value] of [
                ['Level', record.level],
                ['Status', record.status],
                ['Week', record.week],
                ['Recorded', formatDate(record.recorded_at)],
                ['Age', ageText(record.age_seconds)],
                ['Memory ID', record.memory_id],
            ]) {
                const field = document.createElement('div');
                setText(field, `${label}: ${value == null ? 'Not returned' : value}`);
                fields.appendChild(field);
            }
            recalled.appendChild(fields);
            const excerpt = document.createElement('pre');
            excerpt.className = 'excerpt';
            setText(excerpt, record.raw_excerpt || '');
            recalled.appendChild(excerpt);
            if (record.seeded) {
                const badge = document.createElement('span');
                badge.className = 'seeded';
                setText(badge, 'seeded');
                recalled.appendChild(badge);
            }
        }

        const storedList = card.querySelector('.record-list');
        storedList.replaceChildren();
        const memoryRecords = memory.records || [];
        const patientEvents = patient.events || [];
        if (memoryRecords.length) {
            memoryRecords.slice(0, 20).forEach((record) => {
                addLine(
                    storedList,
                    `${formatDate(record.recorded_at)} · ${record.level}/${record.status} · ID ${record.memory_id || 'not returned'}`,
                    Boolean(record.seeded)
                );
            });
        }
        patientEvents.filter((event) => ['store', 'resolve', 'correct'].includes(event.event_type))
            .slice(0, 20).forEach((event) => {
                addLine(
                    storedList,
                    `${formatDate(event.created_at)} · local ${event.event_type} · ${event.level_memory || '—'} · ID ${event.memory_id || 'pending'}`,
                    Boolean((event.note || '').toLowerCase().includes('seeded'))
                );
            });
        if (!storedList.children.length) addLine(storedList, 'No stored memory records returned yet.', false);

        const decision = patient.decision;
        const decisionContent = card.querySelector('.decision-content');
        decisionContent.classList.remove('decision', 'raise');
        if (decision && decision.kind === 'resolved') {
            decisionContent.classList.add('decision');
            setText(
                decisionContent,
                `Recovery confirmed. Meko memory is ${decision.level_memory || 'GREEN'}/resolved; the previous escalation is resolved.`
            );
        } else if (decision && decision.kind === 'corrected') {
            decisionContent.classList.add('decision');
            setText(
                decisionContent,
                `Meko memory was corrected. Current stored level: ${decision.level_memory || '—'}.`
            );
        } else if (decision && decision.kind === 'forgotten') {
            decisionContent.classList.add('decision');
            setText(
                decisionContent,
                'Meko memory was forgotten and will not be used for future triage recalls.'
            );
        } else if (decision && decision.level_ai && decision.level_final) {
            decisionContent.classList.add('decision');
            setText(
                decisionContent,
                `AI said ${decision.level_ai}, Meko memory said ${decision.level_memory || 'nothing'}, final ${decision.level_final}.`
            );
            if ((rank[decision.level_final] || 0) > (rank[decision.level_ai] || 0)) {
                decisionContent.classList.add('raise');
                setText(decisionContent, `Meko raised ${decision.level_ai} to ${decision.level_final}.`);
            }
        } else {
            setText(decisionContent, 'No triage decision recorded yet.');
        }

        const eventList = card.querySelector('.event-list');
        eventList.replaceChildren();
        patientEvents.slice(0, 20).forEach((event) => {
            const li = document.createElement('li');
            const description = document.createElement('span');
            setText(description, `${formatDate(event.created_at)} · ${event.event_type} · ${event.latency_ms || 0} ms`);
            const result = document.createElement('span');
            result.className = event.ok ? 'event-ok' : 'event-fail';
            setText(result, event.ok ? 'OK' : 'FAILED');
            li.append(description, result);
            eventList.appendChild(li);
        });
        if (!eventList.children.length) addLine(eventList, 'No Meko events logged yet.', false);
        card.querySelector('.patient-head h2').textContent = patient.name || 'Patient';
        card.querySelector('.patient-head small').textContent = `Week ${patient.week == null ? '—' : patient.week}`;
    };

    grid.addEventListener('click', async (event) => {
        const card = event.target.closest('.patient');
        if (!card) return;
        if (event.target.classList.contains('correct-button') && event.target.type === 'button') {
            card.querySelector('.correction-form').classList.toggle('open');
        }
        if (event.target.classList.contains('forget-button')) {
            if (!window.confirm('Forget all MatriSetu triage memories for this patient? This cannot be undone.')) return;
            const toast = card.querySelector('.toast');
            try {
                const response = await fetch(`${pageUrl}/${encodeURIComponent(card.dataset.phone)}/forget`, {method: 'POST'});
                const result = await response.json();
                setText(toast, result.ok ? 'Memory forgotten.' : (result.error || 'Meko unavailable.'));
                await refresh();
            } catch (_) {
                setText(toast, 'Meko unavailable. No memory was confirmed deleted.');
            }
        }
    });
    grid.addEventListener('submit', async (event) => {
        if (!event.target.classList.contains('correction-form')) return;
        event.preventDefault();
        const card = event.target.closest('.patient');
        const toast = card.querySelector('.toast');
        const fields = new FormData(event.target);
        try {
            const response = await fetch(`${pageUrl}/${encodeURIComponent(card.dataset.phone)}/correct`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({level: fields.get('level'), status: fields.get('status')}),
            });
            const result = await response.json();
            setText(toast, result.ok ? 'Memory corrected.' : (result.error || 'Meko unavailable.'));
            if (result.ok) await refresh();
        } catch (_) {
            setText(toast, 'Meko unavailable. The correction was not confirmed.');
        }
    });

    async function refresh() {
        try {
            const response = await fetch(`${pageUrl}/data`, {cache: 'no-store'});
            if (!response.ok) return;
            const data = await response.json();
            const status = byId('meko-status');
            setText(status, data.enabled ? 'MEKO ON' : 'MEKO OFF');
            status.classList.toggle('off', !data.enabled);
            byId('off-notice').classList.toggle('off', !data.enabled);
            if (data.summary) {
                for (const [key, id] of [
                    ['recalls', 'count-recalls'], ['stores', 'count-stores'],
                    ['corrections', 'count-corrections'], ['forgets', 'count-forgets'],
                    ['failures', 'count-failures'],
                ]) setText(byId(id), data.summary[key] || 0);
                setText(
                    byId('last-success'),
                    data.summary.last_successful_at
                        ? formatDate(data.summary.last_successful_at)
                        : 'None yet'
                );
            }
            setText(byId('patient-count'), (data.patients || []).length);
            if (!(data.patients || []).length) {
                grid.replaceChildren();
                const message = document.createElement('div');
                message.className = 'patient patient-body muted';
                setText(message, 'No registered patients are assigned to this ASHA worker.');
                grid.appendChild(message);
            } else {
                (data.patients || []).forEach(renderPatient);
                Array.from(grid.children).forEach((card) => {
                    if (card.dataset.phone && !data.patients.some((patient) => patient.phone === card.dataset.phone)) {
                        card.remove();
                    }
                });
            }
        } catch (_) {
            // Keep the last local event log visible if a poll fails.
        }
    }

    if (enabled) refresh();
    window.setInterval(refresh, 5000);
})();
</script>
</body>
</html>
"""


def render_meko_panel(asha_id, patients, events, summary, meko_enabled):
    events_by_phone = {}
    for event in events:
        events_by_phone.setdefault(event["phone"], []).append(event)

    patient_rows = []
    for phone, patient in patients.items():
        patient_events = events_by_phone.get(phone, [])[:20]
        decision = latest_patient_decision(patient_events)
        stored = [
            event for event in patient_events
            if event["event_type"] in {"store", "resolve", "correct"}
        ]
        patient_rows.append({
            "phone": phone,
            "name": patient.get("name") or "Patient",
            "week": patient.get("week"),
            "events": patient_events,
            "stored": stored,
            "decision": decision,
        })

    return render_template_string(
        MEKO_PANEL_HTML,
        asha_id=asha_id,
        patients=patient_rows,
        summary=summary or {},
        meko_enabled=meko_enabled,
    )
