"use client";

import { useEffect, useMemo, useState } from "react";

export type ConsoleStage = {
  id: string;
  index: string;
  label: string;
  shortLabel: string;
  description: string;
  proof: string;
  status: string;
};

export type ConsoleQueueItem = {
  company: string;
  role: string;
  lane: "Apply" | "Apply + Outreach" | "Relationship" | "Watch";
  score: number;
  action: string;
  status: string;
};

type EngineConsoleProps = {
  stages: ConsoleStage[];
  queue: ConsoleQueueItem[];
};

const laneFilters = ["All", "Apply", "Apply + Outreach", "Relationship", "Watch"] as const;

export function EngineConsole({ stages, queue }: EngineConsoleProps) {
  const [activeStage, setActiveStage] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const [laneFilter, setLaneFilter] = useState<(typeof laneFilters)[number]>("All");

  useEffect(() => {
    if (!isRunning) return;

    const timer = window.setInterval(() => {
      setActiveStage((current) => {
        if (current >= stages.length - 1) {
          setIsRunning(false);
          return 0;
        }
        return current + 1;
      });
    }, 850);

    return () => window.clearInterval(timer);
  }, [isRunning, stages.length]);

  const filteredQueue = useMemo(
    () => queue.filter((item) => laneFilter === "All" || item.lane === laneFilter),
    [laneFilter, queue],
  );

  const stage = stages[activeStage];

  function runCycle() {
    setActiveStage(0);
    setIsRunning(true);
  }

  return (
    <section className="engine-console" aria-label="Interactive recruiting engine demo">
      <div className="console-topbar">
        <div className="console-title-group">
          <span className="console-dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>PROD / DAILY-ENGINE</span>
        </div>
        <span className="console-live">
          <i aria-hidden="true" /> Guarded
        </span>
      </div>

      <div className="console-body">
        <div className="console-run-header">
          <div>
            <span className="micro-label">Sanitized product snapshot</span>
            <h2>One decision surface. Two execution lanes.</h2>
          </div>
          <button className="run-button" onClick={runCycle} disabled={isRunning}>
            <span aria-hidden="true">{isRunning ? "•••" : "▶"}</span>
            {isRunning ? "Running cycle" : "Run demo cycle"}
          </button>
        </div>

        <div className="stage-track" role="tablist" aria-label="Engine stages">
          {stages.map((item, index) => (
            <button
              className={index === activeStage ? "stage-button active" : "stage-button"}
              key={item.id}
              onClick={() => {
                setIsRunning(false);
                setActiveStage(index);
              }}
              role="tab"
              aria-selected={index === activeStage}
              aria-controls="active-stage-panel"
            >
              <span>{item.index}</span>
              <strong>{item.shortLabel}</strong>
            </button>
          ))}
        </div>

        <div className="stage-detail" id="active-stage-panel" role="tabpanel" aria-live="polite">
          <div className="stage-detail-copy">
            <span className="micro-label">{stage.status}</span>
            <h3>{stage.label}</h3>
            <p>{stage.description}</p>
          </div>
          <div className="stage-proof">
            <span>Current proof</span>
            <strong>{stage.proof}</strong>
          </div>
        </div>

        <div className="queue-panel">
          <div className="queue-toolbar">
            <div>
              <span className="micro-label">Merged daily queue</span>
              <strong>{filteredQueue.length.toString().padStart(2, "0")} decisions</strong>
            </div>
            <div className="filter-row" aria-label="Filter demo queue by lane">
              {laneFilters.map((lane) => (
                <button
                  className={laneFilter === lane ? "filter-chip selected" : "filter-chip"}
                  key={lane}
                  onClick={() => setLaneFilter(lane)}
                  aria-pressed={laneFilter === lane}
                >
                  {lane}
                </button>
              ))}
            </div>
          </div>

          <div className="queue-table" role="table" aria-label="Fictionalized demo queue">
            <div className="queue-row queue-head" role="row">
              <span role="columnheader">Target</span>
              <span role="columnheader">Lane</span>
              <span role="columnheader">Fit</span>
              <span role="columnheader">Next decision</span>
            </div>
            {filteredQueue.map((item) => (
              <div className="queue-row" role="row" key={`${item.company}-${item.role}`}>
                <span className="queue-target" role="cell">
                  <strong>{item.company}</strong>
                  <small>{item.role}</small>
                </span>
                <span role="cell">
                  <i
                    className={`lane-dot lane-${item.lane
                      .toLowerCase()
                      .replaceAll(" + ", "-plus-")
                      .replaceAll(" ", "-")}`}
                  />
                  {item.lane}
                </span>
                <span className="fit-score" role="cell">
                  {item.score}
                </span>
                <span className="queue-action" role="cell">
                  <strong>{item.action}</strong>
                  <small>{item.status}</small>
                </span>
              </div>
            ))}
          </div>
          <p className="demo-disclaimer">
            Demo companies and roles are fictionalized. System metrics are from real run-scoped
            artifacts.
          </p>
        </div>
      </div>
    </section>
  );
}
