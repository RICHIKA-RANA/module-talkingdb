"""Build the sticky PR comment body from pytest's junit.xml and the
Cobertura coverage summary produced by irongut/CodeCoverageSummary.

Run from the repo root after `pytest --junitxml=junit.xml --cov-report=xml`
and the coverage-summary step, both of which are best-effort (`if: always()`)
so this always has *something* to report even when a prior step failed.
"""

import xml.etree.ElementTree as ET

JUNIT_PATH = "junit.xml"
COVERAGE_SUMMARY_PATH = "code-coverage-results.md"
OUTPUT_PATH = "pr-comment-body.md"


def _test_section() -> str:
    try:
        root = ET.parse(JUNIT_PATH).getroot()
    except (FileNotFoundError, ET.ParseError):
        return "_Test results unavailable (pytest did not produce junit.xml)._"

    suites = root.findall(".//testsuite")
    tests = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    time = sum(float(s.get("time", 0)) for s in suites)
    passed = tests - failures - errors - skipped
    status = (
        "✅ All tests passed"
        if failures == 0 and errors == 0
        else "❌ Some tests failed"
    )

    return "\n".join(
        [
            status,
            "",
            "| Total | Passed | Failed | Errors | Skipped | Time |",
            "|---|---|---|---|---|---|",
            f"| {tests} | {passed} | {failures} | {errors} | {skipped} | {time:.2f}s |",
        ]
    )


def _coverage_section() -> str:
    try:
        with open(COVERAGE_SUMMARY_PATH) as fh:
            return fh.read()
    except FileNotFoundError:
        return "_Coverage report unavailable._"


def main() -> None:
    body = (
        "## 🧪 Test Results\n\n"
        f"{_test_section()}\n\n"
        "## 📊 Coverage Report\n\n"
        f"{_coverage_section()}\n"
    )
    with open(OUTPUT_PATH, "w") as fh:
        fh.write(body)


if __name__ == "__main__":
    main()
