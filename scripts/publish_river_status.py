#!/usr/bin/env python3
"""Publish last verified Argens observation. Preserve previous data during outages."""
import json, math, os, sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATION="Y531201001"
OUTPUT="river-status.json"
NOW=datetime.now(timezone.utc)

def fetch(url):
    with urlopen(Request(url,headers={"User-Agent":"Argens-Alerte/1.0","Accept":"application/json"}),timeout=20) as response:
        return json.load(response)

def stamp(value):
    if isinstance(value,(float,int)):
        return datetime.fromtimestamp(value/(1000 if value>1e11 else 1),timezone.utc)
    t=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

def valid(rows):
    unique={}
    for ts,value in rows:
        try:
            t=stamp(ts)
            v=float(value)
            if math.isfinite(v) and 0<=v<=20000 and NOW-t<=timedelta(hours=4) and t<=NOW+timedelta(minutes=5):
                unique[t]=round(v,2)
        except (TypeError,ValueError,OverflowError):
            continue
    return sorted(unique.items(),reverse=True)

def hubeau():
    params=urlencode({"code_entite":STATION,"grandeur_hydro":"H","size":200,"sort":"desc","date_debut_obs":(NOW-timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    data=fetch("https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr?"+params)
    return valid((r.get("date_obs"),r.get("resultat_obs")) for r in data.get("data",[]) if r.get("code_station")==STATION and r.get("grandeur_hydro")=="H")

def vigicrues():
    params=urlencode({"CdStationHydro":STATION,"GrdSerie":"H","FormatDate":"iso"})
    data=fetch("https://www.vigicrues.gouv.fr/services/observations.json?"+params)
    series=data.get("Serie",{}).get("ObssHydro",[])
    rows=[]
    for item in series:
        if isinstance(item,list) and len(item)>=2: rows.append((item[0],item[1]))
        elif isinstance(item,dict): rows.append((item.get("DtObsHydro") or item.get("date_obs"),item.get("ResObsHydro") if item.get("ResObsHydro") is not None else item.get("resultat_obs")))
    return valid((ts,float(v)*1000) for ts,v in rows if v is not None)

def iso(t): return t.isoformat().replace("+00:00","Z")

def main():
    old={}
    if os.path.exists(OUTPUT):
        try:
            with open(OUTPUT,encoding="utf8") as f: old=json.load(f)
        except (OSError,ValueError): pass
    fresh=None
    for name,loader in (("Hub'Eau",hubeau),("Vigicrues",vigicrues)):
        try:
            series=loader()
            if not series or (NOW-series[0][0])>timedelta(minutes=75):
                raise ValueError("No recent observation")
            latest=series[0]
            prior=next((p for p in sorted((p for p in series[1:] if 40<=(latest[0]-p[0]).total_seconds()/60<=100),key=lambda p:abs((latest[0]-p[0]).total_seconds()/60-60))),None)
            fresh={"time":iso(latest[0]),"mm":latest[1],"prior_time":iso(prior[0]) if prior else None,"prior_mm":prior[1] if prior else None,"source":name}
            print(f"Validated {name}: {fresh['time']} {fresh['mm']} mm")
            break
        except Exception as exc:
            print(f"{name} unavailable: {exc}",file=sys.stderr)
    # Never replace a newer known reading with an older reading.
    previous=old.get("last") if isinstance(old.get("last"),dict) else None
    if fresh and previous:
        try:
            if stamp(previous["time"])>stamp(fresh["time"]):
                fresh=previous
        except (ValueError,TypeError,KeyError): pass
    # Keep historical data even when both upstream APIs fail.
    last=fresh or previous
    payload={"station":STATION,"checked_at":iso(NOW),"source_available":bool(fresh and (NOW-stamp(fresh["time"]))<=timedelta(minutes=75)),"last":last}
    with open(OUTPUT,"w",encoding="utf8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
        f.write("\n")
    print(f"Saved {OUTPUT}; source_available={payload['source_available']}; last={last['time'] if last else 'none'}")

if __name__=="__main__": main()
