/** Fast tier vs deep tier (SPEC §1, §5): who does what, and how fast. Static SVG, theme colors via currentColor. */
export function ArchitectureDiagram() {
  const box = "fill-white/[0.04] stroke-white/25";
  return (
    <figure className="space-y-2">
      <svg
        viewBox="0 0 960 360"
        role="img"
        aria-labelledby="arch-title arch-desc"
        className="h-auto w-full text-foreground"
        fontFamily="inherit"
      >
        <title id="arch-title">Vigil architecture: fast tier and deep tier</title>
        <desc id="arch-desc">
          Devices and the simulator stream into FastAPI, which writes to Tiger Data. The fast tier evaluates rules and
          the priority score in Tiger every 2 seconds and pushes the queue to the UI. Every minute, closed 15-minute
          buckets sync to Snowflake, where anomaly detection, Cortex AI, Cortex Search and Cortex Analyst run; results
          are cached back in Tiger.
        </desc>
        <defs>
          <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" opacity="0.6" />
          </marker>
        </defs>

        {/* sources */}
        <g>
          <rect x="10" y="40" width="170" height="200" rx="14" className={box} />
          <text x="95" y="68" textAnchor="middle" fontSize="15" fontWeight="700" fill="currentColor">Sources</text>
          {["Simulator · 119 residents", "Polar band (Web BT)", "iPhone bed sensor"].map((t, i) => (
            <text key={t} x="95" y={102 + i * 38} textAnchor="middle" fontSize="13" fill="currentColor" opacity="0.8">{t}</text>
          ))}
          <text x="95" y="220" textAnchor="middle" fontSize="12" fill="currentColor" opacity="0.6">WS /ws/ingest · 5 s / 1 s</text>
        </g>
        <line x1="182" y1="140" x2="238" y2="140" stroke="currentColor" strokeOpacity="0.5" strokeWidth="2" markerEnd="url(#arr)" />

        {/* fast tier */}
        <g>
          <rect x="240" y="20" width="330" height="240" rx="16" className="fill-accent/[0.08] stroke-accent/60" strokeWidth="1.5" />
          <text x="405" y="48" textAnchor="middle" fontSize="16" fontWeight="700" className="fill-accent">Fast tier · Tiger Data · seconds</text>
          <text x="405" y="70" textAnchor="middle" fontSize="12" fill="currentColor" opacity="0.7">“what is happening now and how it is trending”</text>
          {[
            "vitals hypertable · columnstore",
            "caggs vitals_1m → 15m → 1h",
            "baselines job (percentile_agg, state_agg)",
            "rules + NEWS2 + priority every 2 s",
            "alert gate: hysteresis, suppression",
          ].map((t, i) => (
            <g key={t}>
              <rect x="262" y={86 + i * 32} width="286" height="26" rx="7" className={box} />
              <text x="405" y={104 + i * 32} textAnchor="middle" fontSize="13" fill="currentColor">{t}</text>
            </g>
          ))}
        </g>

        {/* UI */}
        <line x1="405" y1="262" x2="405" y2="300" stroke="currentColor" strokeOpacity="0.5" strokeWidth="2" markerEnd="url(#arr)" />
        <rect x="285" y="302" width="240" height="44" rx="12" className={box} />
        <text x="405" y="322" textAnchor="middle" fontSize="14" fontWeight="700" fill="currentColor">Rounds Queue · WS /ws/live</text>
        <text x="405" y="339" textAnchor="middle" fontSize="12" fill="currentColor" opacity="0.7">who to check next, and why</text>

        {/* sync + results */}
        <line x1="572" y1="110" x2="636" y2="110" stroke="currentColor" strokeOpacity="0.5" strokeWidth="2" markerEnd="url(#arr)" />
        <text x="604" y="98" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">sync 1 min</text>
        <line x1="636" y1="190" x2="572" y2="190" stroke="currentColor" strokeOpacity="0.5" strokeWidth="2" markerEnd="url(#arr)" />
        <text x="604" y="208" textAnchor="middle" fontSize="11" fill="currentColor" opacity="0.7">ai_insights</text>

        {/* deep tier */}
        <g>
          <rect x="638" y="20" width="312" height="240" rx="16" className="fill-stable-solid/[0.1] stroke-stable-solid/60" strokeWidth="1.5" />
          <text x="794" y="48" textAnchor="middle" fontSize="16" fontWeight="700" className="fill-stable">Deep tier · Snowflake · minutes</text>
          <text x="794" y="70" textAnchor="middle" fontSize="12" fill="currentColor" opacity="0.7">“what it means, what next, what to do”</text>
          {[
            "ML.ANOMALY_DETECTION · 5 models × 120",
            "ML.FORECAST (Phase 9)",
            "Cortex COMPLETE (Claude) · SQL REST API",
            "Cortex Search over care plans · REST",
            "AI_PARSE_DOCUMENT · Cortex Analyst",
          ].map((t, i) => (
            <g key={t}>
              <rect x="656" y={86 + i * 32} width="276" height="26" rx="7" className={box} />
              <text x="794" y={104 + i * 32} textAnchor="middle" fontSize="13" fill="currentColor">{t}</text>
            </g>
          ))}
        </g>
        <text x="794" y="290" textAnchor="middle" fontSize="12" fill="currentColor" opacity="0.7">
          Slow or down? The fast tier and the queue keep working;
        </text>
        <text x="794" y="307" textAnchor="middle" fontSize="12" fill="currentColor" opacity="0.7">
          AI cards fall back to the deterministic template.
        </text>
      </svg>
      <figcaption className="text-sm text-muted-foreground">
        Emergencies and hard rules are caught in Tiger within seconds; slow, silent drift against each resident&apos;s
        own normal is judged in Snowflake per closed 15-minute bucket.
      </figcaption>
    </figure>
  );
}
