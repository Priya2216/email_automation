"""
Command-line reviewer (no API server needed):

    python -m scripts.review_cli list                 # held replies + open hand-offs
    python -m scripts.review_cli show 3
    python -m scripts.review_cli approve 3 --reviewer priya --note "checked with fraud team"
    python -m scripts.review_cli reject 3 --reviewer priya --note "wrong customer"
    python -m scripts.review_cli resolve 4 --reviewer priya
"""
import argparse
import json
import sys

from utils import audit_db, review_service


def main() -> int:
    ap = argparse.ArgumentParser(description="Human review queue")
    ap.add_argument("action", choices=["list", "show", "approve", "reject", "resolve"])
    ap.add_argument("review_id", nargs="?", type=int)
    ap.add_argument("--reviewer", default="cli-reviewer")
    ap.add_argument("--note", default="")
    ap.add_argument("--body-file", help="file with a corrected reply to send instead of the draft (approve)")
    a = ap.parse_args()
    try:
        if a.action == "list":
            for r in audit_db.list_reviews():
                print(f"#{r['id']:<4} {r['kind']:<8} {r['status']:<9} conf={r['overall_confidence']:<5} "
                      f"{r['sender']}  {'; '.join(r['reasons'])[:90]}")
            return 0
        if a.review_id is None:
            ap.error("review_id is required")
        if a.action == "show":
            print(json.dumps(audit_db.get_review(a.review_id), indent=2, ensure_ascii=False, default=str))
            return 0
        if a.action == "approve":
            body = open(a.body_file, encoding="utf-8").read() if a.body_file else None
            result = review_service.approve(a.review_id, a.reviewer, a.note, body)
        elif a.action == "reject":
            result = review_service.reject(a.review_id, a.reviewer, a.note)
        else:
            result = review_service.resolve_handoff(a.review_id, a.reviewer, a.note)
        print(f"#{result['id']} -> {result['status']}  delivered: {result['delivered'] or '-'}")
        return 0
    except review_service.ReviewError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
