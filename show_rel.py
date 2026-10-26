# -*- coding: utf-8 -*-
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db, relia
s = relia.summary(db, 30)
for e in s["recent"][:15]:
    print(time.strftime("%Y-%m-%d %H:%M", time.localtime(e["ts"])), e["kind"], "|", e["label"], "|", (e["detail"] or "")[:150])
