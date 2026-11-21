from eval.s3_enroll_fetch import pick_enrollment


def c(i, start):
    return {"id": i, "start": start}


def test_pick_is_chronological_strict_and_skips_failed_qc():
    cands = [c(1, "2026-08-01T00:00:00Z"), c(2, "2026-08-02T00:00:00Z"), c(3, "2026-08-03T00:00:00Z"),
             c(4, "2026-08-04T00:00:00Z"), c(5, "2026-08-05T00:00:00Z"), c(6, "2026-11-02T00:00:00Z")]
    qc = {4: False, 3: True}
    got = [x["id"] for x in pick_enrollment("2026-08-05T00:00:00Z", cands, qc)]
    assert got == [3, 2, 1]                     # 5 not strictly before, 4 failed QC, unknown 2 / 1 kept
    assert [x["id"] for x in pick_enrollment("2026-12-01T00:00:00Z", cands, {})] == [5, 4, 3]   # never 2026-11
    assert pick_enrollment("2026-08-01T00:00:00Z", cands, {}) == []
