from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import platform
from app.config.settings import settings
from app.storage.brokers import BrokerRepository
from app.storage.schedules import ScheduleRepository
from app.storage.events import WorkspaceEventRepository
from app.jobs.autonomous_scheduler import next_recurrence_utc
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError

@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"sqlite_path",str(tmp_path/"operations.db"))
    monkeypatch.setattr(settings,"broker_secret_key","test-only-key")
    repo=BrokerRepository();connection=repo.save_connection("owner",base_url="https://demo.example",username="secret-user",password="secret-password",server="demo",environment="demo")
    repo.sync_accounts("owner",connection.connection_ref,{"accounts":[{"accountId":"1","accNum":"10"}]})
    profile=repo.create_profile("owner",name="Research",account_ref=repo.list_accounts("owner")[0]["public_id"])
    app.dependency_overrides[platform.current_claims]=lambda:{"sub":"owner"}
    yield repo,profile,TestClient(app)
    app.dependency_overrides.clear()

def test_profile_automation_rename_journal_and_controls(workspace):
    repo,profile,client=workspace
    response=client.patch(f"/api/execution-profiles/{profile['public_id']}",json={"name":"London research","automation":{"decision_provider":"openai","model_identifier":"test-model","execution_mode":"demo_autonomous","cooldown_minutes_after_loss":90}})
    assert response.status_code==200,response.text
    result=response.json();assert result["name"]=="London research"
    assert result["automation"]["model_identifier"]=="test-model"
    assert repo.get_profile("owner",profile["public_id"])["cooldown_minutes_after_loss"]==90
    controls=client.get("/api/autonomous-controls").json()
    assert not controls["demo_autonomous_enabled"] and not controls["live_autonomous_enabled"]
    events=client.get("/api/activity").json()["events"]
    assert any(e["event_type"]=="profile_updated" and e["strategy_name"]=="London research" for e in events)
    assert WorkspaceEventRepository().list("outsider")==[]

def test_paused_schedule_preview_and_deletion(workspace):
    _,profile,client=workspace
    body={"timezone":"Europe/London","recurrence":{"type":"daily","days":["monday","wednesday"],"times":["09:00","14:30"]},"enabled":False}
    response=client.post(f"/api/execution-profiles/{profile['public_id']}/autonomy/schedule",json=body)
    assert response.status_code==200,response.text
    schedule=response.json();body["recurrence"]["times"].append("16:00")
    updated=client.put(f"/api/autonomous-schedules/{schedule['id']}",json=body).json()
    assert updated["enabled"] is False and updated["next_run_at"] is None
    assert updated["recurrence"]["times"]==["09:00","14:30","16:00"]
    preview=client.post("/api/autonomous-schedule-preview",json=body)
    assert preview.status_code==200 and len(preview.json()["next_run_times"])==5
    assert client.post("/api/autonomous-schedule-preview",json={**body,"timezone":"Invalid/Zone"}).status_code==422
    assert client.delete(f"/api/execution-profiles/{profile['public_id']}?confirmation_name=Research").status_code==200
    assert ScheduleRepository().list_schedules("owner")==[]
    kinds={e["event_type"] for e in client.get("/api/activity").json()["events"]}
    assert {"schedule_created","schedule_updated","profile_deleted"}<=kinds

def test_export_excludes_secrets_and_other_owners(workspace):
    repo,_,client=workspace;repo.ensure_user("outsider")
    result=client.get("/api/workspace-export")
    assert result.status_code==200,result.text
    assert len(result.json()["profiles"])==1
    assert "secret-password" not in result.text and "secret-user" not in result.text and "password_encrypted" not in result.text

def test_research_permission_cannot_submit(workspace):
    repo,profile,_=workspace;service=AutonomousDemoService(broker_repository=repo)
    for autonomous in (True,False):
        with pytest.raises(AutonomousExecutionError,match="does not permit"):
            service._require_submission_permission("owner",profile["public_id"],autonomous=autonomous)
    repo.update_profile_v2("owner",profile["public_id"],{"automation":{"execution_mode":"demo_manual"}})
    service._require_submission_permission("owner",profile["public_id"])
    with pytest.raises(AutonomousExecutionError):service._require_submission_permission("owner",profile["public_id"],autonomous=True)

@pytest.mark.parametrize("after,expected",[("2026-01-05T00:00:00+00:00","2026-01-05T08:00:00+00:00"),("2026-07-06T00:00:00+00:00","2026-07-06T07:00:00+00:00")])
def test_session_dst_independent_of_display_timezone(after,expected):
    rule={"type":"market_session","sessions":["london"],"days":["monday"]}
    for zone in ("UTC","America/Chicago","Asia/Tokyo"):
        assert next_recurrence_utc(datetime.fromisoformat(after),zone,rule).isoformat()==expected

def test_overnight_interval_retains_start_day():
    rule={"type":"recurring_interval","start_time":"22:00","end_time":"02:00","interval_minutes":120,"days":["monday"]}
    instant=next_recurrence_utc(datetime(2026,7,6,23,tzinfo=timezone.utc),"UTC",rule)
    assert instant.isoformat()=="2026-07-07T00:00:00+00:00"
