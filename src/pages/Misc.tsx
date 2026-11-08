import { useEffect, useState } from 'react';
import { Activity, ArrowRightLeft, CalendarDays, CheckCircle2, ClipboardList, Droplets, HandMetal, Server, ShieldCheck, Syringe, TrendingUp } from 'lucide-react';
import { appointmentList, inr } from '@/data/mock';
import { StatusPill } from '@/components/StatusPill';
import { BackendBadge } from '@/pages/Registration';
import { useBackend } from '@/hooks/useBackend';
import { getBackendInfo, type BackendInfo } from '@/lib/api';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

export function NursingPage() {
  const [done, setDone] = useState<string[]>([]);
  const tasks = [
    { id: 't1', time: '10:00', patient: 'R. Iyer · GA-10101', task: 'Vitals — BP, pulse, SpO₂', icon: Activity, kind: 'Vitals' },
    { id: 't2', time: '10:30', patient: 'A. Khan · GA-10202', task: 'Medication — Metoprolol 25 mg PO', icon: Syringe, kind: 'MAR' },
    { id: 't3', time: '11:00', patient: 'M. Das · ICU-03', task: 'Intake/output charting', icon: Droplets, kind: 'I/O' },
    { id: 't4', time: '11:30', patient: 'P. Rao · TB-20101', task: 'Nursing assessment — admission', icon: ClipboardList, kind: 'Assessment' },
    { id: 't5', time: '12:00', patient: 'Shift A → Shift B', task: 'Handover — 14 patients, 2 escalations', icon: ArrowRightLeft, kind: 'Handover' },
  ];
  return (
    <div className="mx-auto w-full max-w-[1100px] space-y-5 p-4 lg:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[20px] font-extrabold tracking-tight">Nursing worklist — Shift A</h2>
          <p className="text-[13px] text-muted-foreground">14 assigned patients · Ward A, Twin B · {done.length}/{tasks.length} tasks charted</p>
        </div>
        <button
          onClick={() => toast.info('Escalation sent (mock)', { description: 'Duty doctor paged for ICU-03.' })}
          className="focus-ring rounded-xl border border-rose-300 bg-rose-50 px-4 py-2 text-[13px] font-bold text-rose-700 hover:bg-rose-100"
        >
          Escalate to doctor
        </button>
      </div>
      <div className="card-surface divide-y divide-border/60">
        {tasks.map((t) => {
          const Icon = t.icon;
          const isDone = done.includes(t.id);
          return (
            <div key={t.id} className="flex items-center gap-4 px-5 py-4">
              <span className="w-12 shrink-0 rounded-lg bg-muted px-1.5 py-1 text-center text-[11.5px] font-bold text-slate-600">{t.time}</span>
              <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-xl', isDone ? 'bg-emerald-50 text-emerald-600' : 'bg-accent text-primary')}>
                <Icon className="h-4.5 w-4.5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className={cn('truncate text-[14px] font-semibold', isDone && 'text-slate-400 line-through')}>{t.task}</p>
                <p className="truncate text-[12px] text-muted-foreground">{t.patient} · {t.kind}</p>
              </div>
              <button
                onClick={() => {
                  setDone((d) => (isDone ? d.filter((x) => x !== t.id) : [...d, t.id]));
                  if (!isDone) toast.success('Charted with timestamp and your initials.', { description: 'Signed entries cannot be silently edited — corrections create an amendment.' });
                }}
                aria-pressed={isDone}
                className={cn(
                  'focus-ring flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[12.5px] font-bold',
                  isDone ? 'bg-emerald-50 text-emerald-700' : 'bg-primary text-primary-foreground hover:bg-blue-700',
                )}
              >
                <CheckCircle2 className="h-4 w-4" /> {isDone ? 'Charted' : 'Chart'}
              </button>
            </div>
          );
        })}
      </div>
      <p className="text-[12.5px] text-muted-foreground">All entries are time-stamped and attributable; amendments preserve the original record.</p>
    </div>
  );
}

export function InsurancePage() {
  const claims = [
    { id: 'CLM-7712', patient: 'Kavya Reddy', payer: 'Star Health (TPA: Medi Assist)', amount: 48250, stage: 'Pre-auth approved', tone: 'bg-blue-50 text-blue-700 ring-blue-600/20' },
    { id: 'CLM-7708', patient: 'Arjun Pillai', payer: 'PMJAY · Ayushman Bharat', amount: 86400, stage: 'Submitted · NHCX', tone: 'bg-amber-50 text-amber-700 ring-amber-600/25' },
    { id: 'CLM-7690', patient: 'Suresh Babu', payer: 'HDFC Ergo', amount: 23500, stage: 'Settled', tone: 'bg-emerald-50 text-emerald-700 ring-emerald-600/25' },
  ];
  return (
    <div className="mx-auto w-full max-w-[1100px] space-y-5 p-4 lg:p-6">
      <div>
        <h2 className="text-[20px] font-extrabold tracking-tight">Insurance &amp; TPA claims</h2>
        <p className="text-[13px] text-muted-foreground">TPA, private insurance and government schemes (PMJAY) — tracked from pre-auth to settlement.</p>
      </div>
      <div className="card-surface divide-y divide-border/60">
        {claims.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-4 px-5 py-4">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-primary">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-bold">{c.patient} <span className="ml-1 font-mono text-[11px] font-medium text-slate-400">{c.id}</span></p>
              <p className="text-[12px] text-muted-foreground">{c.payer}</p>
            </div>
            <p className="text-[15px] font-extrabold tabular-nums">{inr(c.amount)}</p>
            <span className={cn('rounded-full px-2.5 py-1 text-[11px] font-bold ring-1 ring-inset', c.tone)}>{c.stage}</span>
            <button onClick={() => toast.info(`Claim ${c.id} workspace opened (mock).`)} className="focus-ring rounded-xl border border-input px-3.5 py-2 text-[12.5px] font-bold text-slate-700 hover:bg-muted">
              Open
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AppointmentsPage() {
  return (
    <div className="mx-auto w-full max-w-[1100px] space-y-5 p-4 lg:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[20px] font-extrabold tracking-tight">Appointments — today</h2>
          <p className="text-[13px] text-muted-foreground">Tokens issued at registration · reminders sent by SMS/WhatsApp.</p>
        </div>
        <button onClick={() => toast.success('Appointment booked (mock)', { description: 'Token G-13 · Dr. Suresh Iyer · 11:40 AM.' })} className="focus-ring flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-[13px] font-bold text-primary-foreground hover:bg-blue-700">
          <CalendarDays className="h-4 w-4" /> Book appointment
        </button>
      </div>
      <div className="card-surface divide-y divide-border/60">
        {appointmentList.map((a) => (
          <div key={a.token} className="flex items-center gap-4 px-5 py-3.5">
            <span className="w-14 shrink-0 rounded-lg bg-muted px-2 py-1.5 text-center text-[12px] font-bold text-slate-600">{a.time}</span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[14px] font-semibold">{a.patient}</p>
              <p className="truncate text-[12px] text-muted-foreground">{a.doctor}</p>
            </div>
            <StatusPill value={a.type === 'New' ? 'In consult' : 'Waiting'} className="hidden sm:inline-flex" />
            <span className="rounded-md bg-accent px-2 py-1 font-mono text-[11px] font-bold text-accent-foreground">{a.token}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ReportsPage() {
  const rows = [
    { label: 'OPD footfall (7d avg)', value: '486 / day', delta: '+6.2%' },
    { label: 'Average waiting time', value: '18 min', delta: '-12%' },
    { label: 'Bed occupancy rate', value: '76%', delta: '+3 pts' },
    { label: 'Avg. length of stay', value: '4.1 days', delta: '-0.4 d' },
    { label: 'Lab turnaround (TAT)', value: '2.4 h', delta: '-18%' },
    { label: 'Pharmacy dispensing time', value: '7 min', delta: '-9%' },
    { label: 'Collection efficiency', value: '94.2%', delta: '+1.1 pts' },
    { label: 'Discharge turnaround', value: '3.2 h', delta: '-22%' },
  ];
  return (
    <div className="mx-auto w-full max-w-[1100px] space-y-5 p-4 lg:p-6">
      <div>
        <h2 className="text-[20px] font-extrabold tracking-tight">Operations dashboard</h2>
        <p className="text-[13px] text-muted-foreground">Hospital administration — volumes, utilization and turnaround metrics.</p>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map((r) => (
          <div key={r.label} className="card-surface p-5">
            <p className="text-[12px] font-semibold text-muted-foreground">{r.label}</p>
            <p className="mt-1.5 text-[22px] font-extrabold tracking-tight">{r.value}</p>
            <p className="mt-1 flex items-center gap-1 text-[11.5px] font-bold text-emerald-600">
              <TrendingUp className="h-3.5 w-3.5" /> {r.delta}
            </p>
          </div>
        ))}
      </div>
      <div className="card-surface p-5">
        <div className="flex items-center gap-2">
          <HandMetal className="h-4 w-4 text-amber-500" />
          <p className="text-[13.5px] font-semibold">Operational alerts</p>
        </div>
        <ul className="mt-3 space-y-2 text-[13px] text-slate-600">
          <li>· ICU occupancy at 75% — 1 escalation pending bed assignment.</li>
          <li>· Troponin-T analyzer QC due today before 2 PM.</li>
          <li>· 3 discharge summaries awaiting consultant sign-off (&gt; 4 h).</li>
        </ul>
      </div>
    </div>
  );
}

export function SettingsPage() {
  const [toggles, setToggles] = useState({ abdm: true, sms: true, whatsapp: true, audit: true, mfa: false });
  const backend = useBackend();
  const [info, setInfo] = useState<BackendInfo | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () => getBackendInfo().then((i) => !cancelled && setInfo(i));
    load();
    const t = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);
  const items = [
    { key: 'abdm' as const, label: 'ABDM integration (sandbox)', desc: 'ABHA creation, verification and care-context linking at registration.' },
    { key: 'sms' as const, label: 'SMS reminders', desc: 'Appointment, report-ready and discharge notifications.' },
    { key: 'whatsapp' as const, label: 'WhatsApp notifications', desc: 'Prescription and bill sharing with patient consent.' },
    { key: 'audit' as const, label: 'Clinical audit trail', desc: 'Immutable log of record access, amendments and sign-offs.' },
    { key: 'mfa' as const, label: 'MFA for privileged roles', desc: 'Required for administrators and record-merge authorization.' },
  ];
  return (
    <div className="mx-auto w-full max-w-[760px] space-y-5 p-4 lg:p-6">
      <div>
        <h2 className="text-[20px] font-extrabold tracking-tight">Settings</h2>
        <p className="text-[13px] text-muted-foreground">Facility-level configuration — backend enforces these regardless of UI state.</p>
      </div>

      {/* Backend connection — reflects the deployment mode chosen by deployment/setup.sh */}
      <div className="card-surface p-5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <Server className="h-4.5 w-4.5 text-primary" />
            <div>
              <h3 className="text-[15px] font-bold">Backend connection</h3>
              <p className="text-[12px] text-muted-foreground">Set at deploy time by <code className="rounded bg-muted px-1 py-0.5 text-[11px]">deployment/setup.sh</code> — local Docker install or remote Frappe server.</p>
            </div>
          </div>
          <BackendBadge live={backend === 'live'} checking={backend === 'checking'} />
        </div>
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-border bg-muted/40 px-4 py-3">
            <p className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Mode</p>
            <p className="mt-1 text-[14px] font-semibold">
              {info ? (info.mode === 'local' ? 'Local · Frappe Health in Docker' : 'Remote · external Frappe server') : 'Not connected'}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-muted/40 px-4 py-3">
            <p className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Frappe target</p>
            <p className="mt-1 truncate font-mono text-[13px] font-semibold text-slate-700">{info?.target ?? '—'}</p>
          </div>
        </div>
        {info && info.frappe === 'down' && (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[12px] font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
            The BFF is running but Frappe is unreachable at {info.target} — check the API keys and that the server is up.
          </p>
        )}
        <p className="mt-3 text-[12px] text-muted-foreground">
          Switch modes on the host: <code className="rounded bg-muted px-1 py-0.5 text-[11px]">./deployment/setup.sh local</code> installs Frappe Health in Docker;{' '}
          <code className="rounded bg-muted px-1 py-0.5 text-[11px]">./deployment/setup.sh remote &lt;url&gt;</code> points the BFF at an existing server.
        </p>
      </div>

      <div className="card-surface divide-y divide-border/60">
        {items.map((it) => (
          <div key={it.key} className="flex items-center gap-4 px-5 py-4">
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-semibold">{it.label}</p>
              <p className="text-[12px] text-muted-foreground">{it.desc}</p>
            </div>
            <button
              role="switch"
              aria-checked={toggles[it.key]}
              aria-label={it.label}
              onClick={() => {
                setToggles((t) => ({ ...t, [it.key]: !t[it.key] }));
                toast.success(`${it.label} ${toggles[it.key] ? 'disabled' : 'enabled'} (mock).`);
              }}
              className={cn('focus-ring relative h-6 w-11 shrink-0 rounded-full transition-colors', toggles[it.key] ? 'bg-primary' : 'bg-slate-300')}
            >
              <span className={cn('absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all', toggles[it.key] ? 'left-[22px]' : 'left-0.5')} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
