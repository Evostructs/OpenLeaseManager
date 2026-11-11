"""Offline transport integrity checks. No database, credentials or extraction."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

FILES = {'manifest.json', 'snapshot.json', 'tables.json', 'finance-handoff.xlsx', 'README.txt'}
MAX_BYTES = 50 * 1024 * 1024


def verify_package(content: bytes) -> dict:
    if len(content) > MAX_BYTES:
        raise ValueError('交接包超过50MB限制')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if len(names) != len(FILES) or set(names) != FILES:
                raise ValueError('交接包文件不完整、重复或包含未知路径')
            if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
                raise ValueError('交接包解压大小超过50MB限制')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest['schema'] != 'FINANCE_HANDOFF_V1' or set(manifest['files']) != FILES - {'manifest.json'}:
                raise ValueError('交接包版本或文件清单不匹配')
            for name, proof in manifest['files'].items():
                data = archive.read(name)
                if len(data) != proof['bytes'] or hashlib.sha256(data).hexdigest() != proof['sha256']:
                    raise ValueError('文件校验失败：'+name)
            snapshot = json.loads(archive.read('snapshot.json'))
            tables = json.loads(archive.read('tables.json'))
            if snapshot['schema'] != manifest['schema'] or tables['schema'] != manifest['schema']:
                raise ValueError('交接包内部版本不一致')
            facts = snapshot['facts']
            canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if hashlib.sha256(canonical.encode('utf-8')).hexdigest() != manifest['source_digest']:
                raise ValueError('来源快照摘要不匹配')
            if 'handoff_digest' in manifest:
                context = {k: v for k, v in snapshot.items() if k != 'schema'}
                canonical_context = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
                if hashlib.sha256(canonical_context.encode('utf-8')).hexdigest() != manifest['handoff_digest'] or context['customer_mapping'] != manifest['customer_mapping'] or tables['customer_mapping'] != context['customer_mapping']:
                    raise ValueError('交接映射或完整快照摘要不一致')
                if manifest.get('mapping_required') and (manifest['mode'] != 'closed' or not context['customer_mapping']['ready']):
                    raise ValueError('附客户映射的交接缺少确认或独立关账')
            if any(snapshot[key] != manifest[key] for key in ('building', 'month', 'mode', 'revision', 'status', 'submitted_by', 'reviewed_by')):
                raise ValueError('交接摘要与快照不一致')
            if facts['month'] != manifest['month'] or manifest['mode'] not in {'draft', 'closed'}:
                raise ValueError('交接月份或类型无效')
            for key, count in manifest['counts'].items():
                if len(facts[key]) != count:
                    raise ValueError('明细计数不匹配：'+key)
            if manifest['mode'] == 'closed':
                reviewed = any(e['action'] == 'close' and e['revision'] == manifest['revision'] and e['actor'] == manifest['reviewed_by'] for e in snapshot['events'])
                if manifest['status'] != 'closed' or not manifest['submitted_by'] or not manifest['reviewed_by'] or manifest['submitted_by'] == manifest['reviewed_by'] or not reviewed:
                    raise ValueError('关账交接缺少独立复核依据')
            return {'status': 'PASS', 'schema': manifest['schema'], 'month': manifest['month'], 'mode': manifest['mode'],
                    'revision': manifest['revision'], 'counts': manifest['counts'], 'package_sha256': hashlib.sha256(content).hexdigest(),
                    'note': '传输完整性核验，不等同签名或真实性认证'}
    except (KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError('无法校验交接包结构或文件内容') from exc
