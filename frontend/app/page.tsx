import ReportGate from "@/components/ReportGate";

export default function HomePage() {
  return (
    <div className="space-y-8">
      <div className="max-w-3xl">
        <h1 className="text-balance text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Report a civic problem. One message starts the whole workflow.
        </h1>
        <p className="mt-3 text-pretty text-sm leading-relaxed text-mute sm:text-base">
          You send a sentence, a photo and a location. BarathSeva AI
          authenticates the evidence, verifies the issue, classifies it, maps it
          to your ward, finds related reports nearby, routes it to the
          responsible department, opens a ticket and holds that ticket to a
          deadline — without you having to work out which agency owns the
          problem.
        </p>
      </div>
      <ReportGate />
    </div>
  );
}
