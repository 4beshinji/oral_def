from backend.tests.history_fixture import append_turns, seed_history
from backend.tests.test_drill import PACK
from backend.tests.test_persistence import make_client


def test_history_pages_keep_old_audio_and_assessments_after_new_turns_and_reload(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        sid = client.post("/v1/sessions", json={"pack": PACK}).json()["session_id"]
        first = seed_history(app.state.database, sid)
        snapshot = client.get(f"/v1/sessions/{sid}").json()
        assert snapshot["turns_total"] == snapshot["confirmed_turns_total"] == 51
        assert [t["ordinal"] for t in snapshot["turns"]] == list(range(2, 52))
        assert len(snapshot["requests"]) == len(snapshot["playbacks"]) == 50
        assert snapshot["requests_total"] == snapshot["playbacks_total"] == 60
        append_turns(app.state.database, sid, 1)
        page = client.get(f"/v1/sessions/{sid}/turns?before={snapshot['turns_before']}").json()
        assert [t["ordinal"] for t in page["turns"]] == [1]
        assert page["next_before"] is None
        turn = page["turns"][0]
        assert turn["has_recording"] is True  # Recording lies outside visible exercises.
        assert turn["exercises_total"] == 23
        assert len(turn["exercises"]) == 20
        assert client.get(f"/v1/sessions/{sid}/turns/{first['turn_id']}").json() == turn
        old_exercises = client.get(
            f"/v1/turns/{first['turn_id']}/exercises?before={turn['exercises_before']}"
        ).json()
        exercise = old_exercises["items"][0]
        assert exercise["id"] == first["exercise_id"]
        assert exercise["attempts_total"] == 23
        assert len(exercise["attempts"]) == 20
        old_attempts = client.get(
            f"/v1/exercises/{exercise['id']}/attempts?before={exercise['attempts_before']}"
        ).json()
        attempt = old_attempts["items"][0]
        assert attempt["id"] == first["attempt_id"]
        assert attempt["audio_available"] is True
        assert attempt["assessment"]["evidence_status"] == "unavailable"
        assessments = client.get(f"/v1/attempts/{attempt['id']}/assessments").json()
        assert assessments["total"] == 23
        assert len(assessments["items"]) == 20
        old_runs = client.get(
            f"/v1/attempts/{attempt['id']}/assessments?before={assessments['next_before']}"
        ).json()
        assert old_runs["next_before"] is None
        assert len({r["id"] for r in assessments["items"] + old_runs["items"]}) == 23
        other = client.post("/v1/sessions", json={"pack": PACK}).json()["session_id"]
        assert client.get(f"/v1/sessions/{other}/turns/{first['turn_id']}").status_code == 404

    _, reloaded = make_client(tmp_path)
    with reloaded:
        assert reloaded.get(f"/v1/sessions/{sid}").json()["turns_total"] == 52
        assert reloaded.get(f"/v1/sessions/{sid}/turns/{first['turn_id']}").json() == turn
        assert reloaded.get(f"/v1/audio/{first['audio_id']}").status_code == 200
        assert reloaded.delete(f"/v1/sessions/{sid}").status_code == 200
        assert reloaded.get(f"/v1/attempts/{first['attempt_id']}/assessments").status_code == 404


def test_nested_cursors_remain_stable_when_new_attempt_is_added(tmp_path):
    from backend.app.db import now, uid

    app, client = make_client(tmp_path)
    with client:
        sid = client.post("/v1/sessions", json={"pack": PACK}).json()["session_id"]
        first = seed_history(app.state.database, sid)
        path = f"/v1/exercises/{first['exercise_id']}/attempts"
        page = client.get(path).json()
        with app.state.database.connect() as db:
            db.execute(
                "INSERT INTO attempts (id,session_id,exercise_id,audio_meta_json,created_at) VALUES (?,?,?,'{}',?)",
                (uid(), sid, first["exercise_id"], now()),
            )
        older = client.get(f"{path}?before={page['next_before']}").json()
        assert older["total"] == 24
        assert older["next_before"] is None
        assert len({a["id"] for a in page["items"] + older["items"]}) == 23
