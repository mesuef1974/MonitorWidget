# -*- coding: utf-8 -*-
"""ملفات الإعدادات JSON (سياسة التنظيف، إعدادات MachineCare): قراءة وكتابة ذرية عبر atomic_json مع نسخة احتياطية واحدة
لكل ملف في كل تشغيل للعملية تُحفظ في <ROOT>/backups قبل أول كتابة (كانت هذه الدوال في server.py؛ البند 6/ج)."""
import os
import shutil
import time

import atomic_json
from paths import ROOT

_backed_up = set()


def read_json(path):
    return atomic_json.read(path)


def _backup_json(path):
    if path not in _backed_up:
        bdir = os.path.join(ROOT, "backups")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy(path, os.path.join(bdir, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(path)))
        _backed_up.add(path)


def write_json(path, bom, obj):
    with atomic_json.locked(path):
        _backup_json(path)
        atomic_json.write(path, bom, obj)


def edit_json(path):
    """معاملة قراءة-تعديل-كتابة: `with edit_json(p) as (bom, obj): ...`؛ النسخة الاحتياطية قبل الكتابة الأولى فقط."""
    return atomic_json.transaction(path, before_write=_backup_json)
