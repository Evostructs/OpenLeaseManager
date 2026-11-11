from datetime import date

from sqlalchemy import event

from app.database import SessionLocal
from app.models import Building, Contract, Customer
from tests.test_auth_integrity import _client, _ensure_admin, _login


def test_customer_sort_preserves_display_names_and_loads_only_requested_page():
    with _client() as client:
        _ensure_admin(client)
        _login(client, 'admin', 'Admin123')
        expected = []
        with SessionLocal() as db:
            bid = db.query(Building).filter_by(code='DEMO_A').one().id
            for i in range(80):
                # Linked names deliberately oppose the stale contract name.
                name = f'客户{79-i:03}' if i % 2 else f'Legacy {i:03}'
                customer = Customer(name=name) if i % 2 else None
                if customer:
                    db.add(customer); db.flush()
                contract = Contract(building_id=bid, contract_no=f'SORT-CAP-{i:03}',
                    customer_id=customer.id if customer else None,
                    tenant_name=f'Stale {i:03}' if customer else name,
                    start_date=date(2026, 1, 1), end_date=date(2028, 12, 31))
                db.add(contract); db.flush()
                expected.append((name, contract.id))
            db.commit()
        loaded = []

        def on_load(contract, _context):
            loaded.append(contract.id)

        event.listen(Contract, 'load', on_load)
        try:
            for direction in ('asc', 'desc'):
                loaded.clear()
                response = client.get('/api/contracts', params={
                    'building': 'DEMO_A', 'keyword': 'SORT-CAP-', 'page': 2,
                    'page_size': 5, 'sort_by': 'tenant_name', 'sort_dir': direction})
                assert response.status_code == 200
                data = response.json()
                ordered = sorted(expected, key=lambda row: row[0], reverse=direction == 'desc')
                assert data['total'] == 80
                assert [row['id'] for row in data['items']] == [row[1] for row in ordered[5:10]]
                assert len(loaded) == 5
        finally:
            event.remove(Contract, 'load', on_load)
