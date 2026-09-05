"""Runs ats-resume-scorer over one PDF and prints its report as one JSON line.

Executed by ats_external.scorer_report inside a separate Python 3.12
environment, so it must not import anything from cv_maker. Usage:

    python _scorer_runner.py <cv.pdf> <job_description.txt>
"""
import json
import logging
import sys
import warnings


def main() -> int:
    pdf_path, jd_path = sys.argv[1], sys.argv[2]
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")

    from ats_resume_scorer import ATSResumeScorer

    with open(jd_path, encoding="utf-8") as handle:
        job_description = handle.read()
    report = ATSResumeScorer().score_resume(pdf_path, job_description)
    keep = ("overall_score", "grade", "detailed_breakdown", "resume_summary", "recommendations")
    print(json.dumps({k: report.get(k) for k in keep}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
