from app.storage.user_experience import UserExperienceRepository


def test_default_watchlist_and_user_scoped_persistence(tmp_path):
    repository=UserExperienceRepository(tmp_path/"ux.db")
    default=repository.list_watchlists("user-a")[0]
    assert default["name"]=="Forex Majors" and len(default["items"])==7
    custom=repository.create_watchlist("user-a","Metals",["OANDA:XAU_USD","OANDA:XAG_USD"])
    changed=repository.replace_watchlist("user-a",custom["id"],name="Pinned Metals",items=[
        {"symbol":"OANDA:XAG_USD","pinned":True},{"symbol":"OANDA:XAU_USD","pinned":False}])
    assert changed and changed["items"][0]["symbol"]=="OANDA:XAG_USD" and changed["items"][0]["pinned"] is True
    assert repository.replace_watchlist("user-b",custom["id"],name=None,items=[]) is None
    assert repository.delete_watchlist("user-a",default["id"]) is False
    assert repository.delete_watchlist("user-a",custom["id"]) is True


def test_preferences_and_notifications_are_durable_and_isolated(tmp_path):
    path=tmp_path/"ux.db";repository=UserExperienceRepository(path)
    changed=repository.update_preferences("user-a",{"timezone":"Europe/London","appearance":"dark",
        "notifications":{"schedule_failed":False}})
    assert changed["timezone"]=="Europe/London" and changed["appearance"]=="dark"
    assert changed["notifications"]["schedule_failed"] is False
    assert UserExperienceRepository(path).preferences("user-a")["timezone"]=="Europe/London"
    assert repository.preferences("user-b")["timezone"]=="America/Chicago"
