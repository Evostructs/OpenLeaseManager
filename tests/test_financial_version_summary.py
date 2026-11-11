import json
from sqlalchemy import event
from app.database import SessionLocal, engine
from app.models import Building, FinancialPlanVersion
from app.services.financial_planning_store import list_versions, version_dict


def test_projected_history_matches_full_summaries_and_keeps_scope_and_details():
    with SessionLocal() as db:
        buildings = db.query(Building).order_by(Building.id).all()
        rows = []
        for building, year, revision, snapshot in (
            (buildings[0], 2026, 1, {'totals': {'revenue': 1.234567}, 'budget_version_id': 17}),
            (buildings[0], 2026, 2, {}),
            (buildings[0], 2026, 3, {'totals': None}),
            (buildings[1], 2026, 1, {'totals': {'revenue': 99}}),
            (buildings[0], 2027, 1, {'totals': {'revenue': 88}}),
        ):
            snapshot['sources'] = [{'label':'synthetic full detail','months':list(range(2000))}]
            row=FinancialPlanVersion(building_id=building.id, year=year, revision=revision, kind='forecast', name='Synthetic', client_state_json='{"kept":true}', snapshot_json=json.dumps(snapshot), source_digest='0'*64, created_by='test')
            db.add(row); rows.append(row)
        db.commit()
        expected = [version_dict(row, summary=True) for row in reversed(rows[:3])]
        statements=[]
        def collect(conn, cursor, statement, params, context, many): statements.append(statement)
        event.listen(engine,'before_cursor_execute',collect)
        try: actual=list_versions(db, buildings[0].id,2026)
        finally: event.remove(engine,'before_cursor_execute',collect)
        assert actual == expected
        # The query must not fetch client-state blobs or whole snapshot columns.
        sql=next(s for s in statements if 'financial_plan_versions' in s)
        assert 'client_state_json' not in sql
        assert 'json_extract' in sql
        assert version_dict(rows[0])['snapshot']['sources'][0]['months'] == list(range(2000))
        assert version_dict(rows[0])['client_state'] == {'kept':True}
