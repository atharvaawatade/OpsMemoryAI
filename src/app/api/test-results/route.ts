import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export const dynamic = "force-dynamic";

interface TestDetail {
  id: string;
  name: string;
  class: string;
  status: "PASS" | "FAIL" | "ERROR" | "SKIP";
  elapsed_s: number;
  message: string;
}

interface SuiteSummary {
  suite_id: string;
  suite_name: string;
  status: "PASS" | "FAIL" | "ERROR";
  tests: TestDetail[];
  summary: { total: number; passed: number; failed: number; errors: number; skipped: number };
  elapsed_s: number;
}

interface TestReport {
  run_at: string;
  overall_status: "PASS" | "FAIL";
  elapsed_s: number;
  environment: Record<string, string>;
  summary: {
    total: number;
    passed: number;
    failed: number;
    errors: number;
    skipped: number;
    pass_rate: string;
  };
  suites: SuiteSummary[];
}

export async function GET() {
  // Resolve testing/logs/latest.json relative to project root
  const logsPath = path.join(process.cwd(), "testing", "logs", "latest.json");

  try {
    if (!fs.existsSync(logsPath)) {
      return NextResponse.json({ error: "No test results found. Run: python3 testing/run_all_tests.py" }, { status: 404 });
    }

    const raw = fs.readFileSync(logsPath, "utf-8");
    const report: TestReport = JSON.parse(raw);
    return NextResponse.json(report);
  } catch (err: any) {
    return NextResponse.json({ error: err.message ?? "Failed to read test results" }, { status: 500 });
  }
}
