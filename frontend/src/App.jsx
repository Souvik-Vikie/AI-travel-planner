import { useEffect, useRef, useState } from "react";
import { getMeta, sendDecision, startTrip } from "./api.js";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];
const inr = (n) => "₹" + Math.round(n ?? 0).toLocaleString("en-IN");

// Map each reasoning line to a pipeline stage (for the progress rail and feed icons).
const STAGES = [
  { key: "research", label: "Research", match: /^Researched/ },
  { key: "choose", label: "Choose", match: /^Picked/ },
  { key: "flights", label: "Flights", match: /^Searched/ },
  { key: "hotels", label: "Hotels", match: /^Found/ },
  { key: "budget", label: "Budget check", match: /^Budget check/ },
  { key: "itinerary", label: "Itinerary", match: /^Built/ },
  { key: "approval", label: "Your approval", match: /^Human|^Plan changed/ },
  { key: "booking", label: "Booking", match: /^Booked|booking failed|sold out/i },
  { key: "pdf", label: "PDF", match: /^Trip plan PDF/ },
];
const stageOf = (text) =>
  /^Re-plan/.test(text) ? "replan" : STAGES.find((s) => s.match.test(text))?.key ?? "info";



export default function App() {
  const [meta, setMeta] = useState(null);
  const [form, setForm] = useState({
    origin_city: "Delhi", region: "any", month: ((new Date().getMonth() + 1) % 12) + 1, start_day: 10,
    days: 5, travellers: 2, budget: 80000, interests: ["beach", "food"],
  });
  const [log, setLog] = useState([]);
  const [trip, setTrip] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getMeta().then(setMeta).catch(() => setError("Cannot reach the backend. Is `uvicorn travel_agent.api:app` running?"));
  }, []);

  const onEvent = (ev) => {
    if (ev.type === "log") setLog((l) => [...l, ev.text]);
    else if (ev.type === "result") setTrip(ev);
    else if (ev.type === "error") setError(ev.text);
  };

  const run = async (fn) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const plan = () => {
    setLog([]);
    setTrip(null);
    run(() => startTrip(form, onEvent));
  };
  const decide = (decision, feedback = "", destination_id = "") =>
    run(() => sendDecision(trip.thread_id, { decision, feedback, destination_id }, onEvent));

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">W</span>
          <div>
            <h1>Wayfarer</h1>
            <p>Agentic travel planner · LangGraph + LangChain</p>
          </div>
        </div>
        {meta && (
          <div className="pills">
            <span className="pill">LLM: {meta.llm}</span>
            <span className="pill">Flights: {meta.flight_provider}</span>
            <span className="pill">Hotels: {meta.hotel_provider}</span>
          </div>
        )}
      </header>

      <main className="layout">
        <TripForm form={form} setForm={setForm} meta={meta} busy={busy} onSubmit={plan} />

        <section className="workspace">
          {error && <div className="alert">{error}</div>}
          {!log.length && !busy && !trip && <Welcome meta={meta} />}
          {(log.length > 0 || busy) && <Progress log={log} trip={trip} busy={busy} />}
          {trip?.status === "awaiting_approval" && <ApprovalCard trip={trip} busy={busy} onDecide={decide} />}
          {trip?.status === "done" && <DoneCard trip={trip} />}
          {trip?.status === "cancelled" && <div className="card muted">Trip cancelled. Nothing was booked.</div>}
          {trip?.status === "no_feasible_plan" && (
            <div className="card warn">
              <h3>No plan fits this budget</h3>
              <p>{trip.log.at(-1)}</p>
            </div>
          )}
          {trip?.candidates?.length > 0 && (
            <Candidates trip={trip} busy={busy} onPick={(id) => decide("switch", "", id)} />
          )}
        </section>
      </main>
    </div>
  );
}

function TripForm({ form, setForm, meta, busy, onSubmit }) {
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value });
  const toggle = (tag) =>
    setForm({ ...form, interests: form.interests.includes(tag) ? form.interests.filter((t) => t !== tag) : [...form.interests, tag] });

  return (
    <form className="card form" onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
      <h2>Plan a trip</h2>

      <label>From
        <select value={form.origin_city} onChange={set("origin_city")}>
          {(meta?.origins ?? ["Delhi"]).map((c) => <option key={c}>{c}</option>)}
        </select>
      </label>

      <div className="field">
        <span>Where</span>
        <div className="segmented">
          {[["india", "India"], ["abroad", "Abroad"], ["any", "Surprise me"]].map(([v, l]) => (
            <button type="button" key={v} className={form.region === v ? "on" : ""}
              onClick={() => setForm({ ...form, region: v })}>{l}</button>
          ))}
        </div>
      </div>

      <div className="row2">
        <label>Month
          <select value={form.month} onChange={(e) => setForm({ ...form, month: Number(e.target.value) })}>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
        </label>
        <label>Start day
          <input type="number" min="1" max="28" value={form.start_day} onChange={set("start_day")} />
        </label>
      </div>

      <div className="row2">
        <label>Days
          <input type="number" min="2" max="21" value={form.days} onChange={set("days")} />
        </label>
        <label>Travellers
          <input type="number" min="1" max="9" value={form.travellers} onChange={set("travellers")} />
        </label>
      </div>

      <label>Total budget (INR)
        <input type="number" min="1000" step="1000" value={form.budget} onChange={set("budget")} />
        <small>{inr(form.budget)} for everything: flights, hotel, food, local travel</small>
      </label>

      <div className="field">
        <span>Interests</span>
        <div className="chips">
          {(meta?.interests ?? []).map((t) => (
            <button type="button" key={t} className={form.interests.includes(t) ? "chip on" : "chip"}
              onClick={() => toggle(t)}>{t}</button>
          ))}
        </div>
      </div>

      <button className="primary" disabled={busy}>{busy ? "Agent is working…" : "Find my trip"}</button>
      <p className="fine">Nothing gets booked until you approve the plan.</p>
    </form>
  );
}

function Welcome({ meta }) {
  return (
    <div className="card welcome">
      <h2>Tell the agent your budget and month. It does the rest.</h2>
      <ol>
        <li>Ranks {meta?.destinations ?? 31} destinations on season, cost, travel time and your interests</li>
        <li>Searches flights ±2 days around your date and picks well-rated hotels</li>
        <li>A budget critic forces a re-plan if the trip costs too much</li>
        <li>You approve. Only then does it book and write your PDF itinerary.</li>
      </ol>
    </div>
  );
}

function Progress({ log, trip, busy }) {
  const reached = new Set(log.map(stageOf));
  const end = useRef(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [log.length]);
  const waiting = trip?.status === "awaiting_approval";
  return (
    <div className="card">
      <div className="rail">
        {STAGES.map((s) => (
          <div key={s.key} className={`step ${reached.has(s.key) ? "done" : ""} ${waiting && s.key === "approval" ? "now" : ""}`}>
            <span className="dot" />{s.label}
          </div>
        ))}
      </div>
      <h3>Agent reasoning</h3>
      <ul className="feed">
        {log.map((line, i) => {
          const st = stageOf(line);
          return (
            <li key={i} className={`ev ev-${st} ${/FAILED|failed|REJECTED/.test(line) ? "bad" : ""}`}>
              <span className="ic" /><span>{line}</span>
            </li>
          );
        })}
        {busy && <li className="ev thinking"><span className="spinner" /> thinking…</li>}
        <li ref={end} />
      </ul>
    </div>
  );
}

function CostBar({ cost, budget }) {
  const parts = [["flights", "Flights"], ["hotel", "Hotel"], ["daily_expenses", "Food & local"], ["buffer", "Buffer"]];
  const scale = Math.max(cost.total, budget);
  return (
    <div className="costbar">
      <div className="bar">
        {parts.map(([k]) => <div key={k} className={`seg seg-${k}`} style={{ width: `${(100 * cost[k]) / scale}%` }} />)}
        <div className="budget-line" style={{ left: `${(100 * budget) / scale}%` }} title="Budget" />
      </div>
      <div className="legend">
        {parts.map(([k, l]) => <span key={k}><i className={`sw seg-${k}`} />{l} {inr(cost[k])}</span>)}
      </div>
    </div>
  );
}

function ApprovalCard({ trip, busy, onDecide }) {
  const a = trip.approval;
  const [feedback, setFeedback] = useState("");
  const over = !a.within_budget;
  return (
    <div className="card approval">
      <div className="approval-head">
        <div>
          <span className="eyebrow">Proposed plan · waiting for your approval</span>
          <h2>{a.destination}</h2>
          <p className="dates">{a.dates}</p>
        </div>
        <div className={`total ${over ? "over" : ""}`}>
          <strong>{inr(a.cost.total)}</strong>
          <span>{over ? "over" : "of"} {inr(a.budget)} budget</span>
        </div>
      </div>
      <p className="reason">{a.reason}</p>
      <CostBar cost={a.cost} budget={a.budget} />
      <div className="facts">
        <div><span>Flight</span>{a.flight}</div>
        <div><span>Hotel</span>{a.hotel}</div>
      </div>
      {trip.flight_meta?.chosen_offset ? (
        <p className="note">Dates shifted {trip.flight_meta.chosen_offset > 0 ? "+" : ""}{trip.flight_meta.chosen_offset} day(s) to save {inr(trip.flight_meta.flex_saving)} on flights.</p>
      ) : null}
      <Itinerary days={trip.itinerary} />

      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => onDecide("approve")}>Approve &amp; book</button>
        <button className="ghost" disabled={busy} onClick={() => onDecide("cancel")}>Cancel trip</button>
      </div>
      <div className="reject">
        <span>Not quite right? Tell the agent:</span>
        <div className="chips">
          {["Too expensive, make it cheaper", "Somewhere else please", "I want a nicer hotel"].map((t) => (
            <button key={t} className="chip" disabled={busy} onClick={() => onDecide("reject", t)}>{t}</button>
          ))}
        </div>
        <div className="inline">
          <input placeholder="Or write your own feedback…" value={feedback} onChange={(e) => setFeedback(e.target.value)} />
          <button disabled={busy || !feedback.trim()} onClick={() => onDecide("reject", feedback)}>Re-plan</button>
        </div>
      </div>
    </div>
  );
}

function Itinerary({ days, full }) {
  if (!days?.length) return null;
  return (
    <div className="itinerary">
      {days.map((d) => (
        <div key={d.day} className="day">
          <div className="day-num">Day {d.day}<small>{d.date}</small></div>
          <div>
            <strong>{d.title}</strong>
            {full ? (
              <ul>
                {["morning", "afternoon", "evening"].map((p) => d[p] && <li key={p}><em>{p}</em> {d[p]}</li>)}
              </ul>
            ) : (
              <p>{[d.morning, d.afternoon].filter(Boolean).join(" · ")}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function DoneCard({ trip }) {
  const b = trip.bookings;
  const m = trip.metrics;
  return (
    <div className="card success">
      <span className="eyebrow">Booked ✓</span>
      <h2>You're going to {trip.destination.name}!</h2>
      <div className="facts">
        <div><span>Flight ref</span>{b.flight.reference} <small>({b.flight.mode})</small></div>
        <div><span>Hotel ref</span>{b.hotel.reference} <small>({b.hotel.mode})</small></div>
        <div><span>Total</span>{inr(trip.cost.total)} of {inr(trip.request.budget)}</div>
        <div><span>Saved vs first-found</span>{inr(m.savings_vs_first_found)} ({Math.round(100 * m.savings_pct)}%)</div>
      </div>
      <a className="primary as-btn" href={trip.pdf_url} target="_blank" rel="noreferrer">Download PDF itinerary</a>
      <Itinerary days={trip.itinerary} full />
    </div>
  );
}

function Candidates({ trip, busy, onPick }) {
  const chosen = trip.destination?.id;
  const canPick = trip.status === "awaiting_approval";
  return (
    <div className="card">
      <h3>Destinations the agent considered</h3>
      <p className="hint">
        {canPick
          ? "Prefer another place? Pick it and the agent re-plans flights, hotel and itinerary for it (each switch runs a fresh search)."
          : "Booked trips can't be switched. Plan a new trip to explore another destination."}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Destination</th><th>Score</th><th>Season</th><th>Est. cost</th><th>Flight</th><th /></tr>
          </thead>
          <tbody>
            {trip.candidates.map((c) => (
              <tr key={c.id} className={c.id === chosen ? "chosen" : ""}>
                <td>{c.name}<small>{c.country}</small></td>
                <td><div className="score"><div style={{ width: `${c.score * 100}%` }} /></div>{c.score.toFixed(2)}</td>
                <td>{c.climate?.label}</td>
                <td>{inr(c.estimated_cost)}</td>
                <td>{c.flight_hours}h</td>
                <td>
                  {c.id === chosen ? <span className="sel">Selected</span> : canPick && (
                    <button className="pick" disabled={busy} onClick={() => onPick(c.id)}>Choose</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
