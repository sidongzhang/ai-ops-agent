// Native SQLite journal, not an agent scheduler. Never persist credentials/capabilities.
import { chmodSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
export class RunStore {
  constructor(path=':memory:') {
    this.db=new DatabaseSync(path);
    if(path!==':memory:')chmodSync(path,0o600);
    this.token=randomUUID();
    this.db.exec('PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, token TEXT NOT NULL)');
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const old=this.db.prepare('SELECT * FROM owner WHERE id=1').get();
      if(old){let alive=false;try{process.kill(old.pid,0);alive=true;}catch(e){if(e.code!=="ESRCH")alive=true;}if(alive)throw new Error('Pi state store already owned');}
      this.db.prepare('INSERT INTO owner VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,token=excluded.token').run(process.pid,this.token);
      this.db.exec('COMMIT');
    }catch(error){this.db.exec('ROLLBACK');this.db.close();throw error;}
    this.db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, snapshot TEXT NOT NULL)');
    for(const row of this.db.prepare('SELECT id,digest,snapshot FROM runs').all()) {
      const state=JSON.parse(row.snapshot);
      if(state.performance?.expires_at<=Date.now()){delete state.performance;this.save(state,row.digest);}
      if(['running','stopping'].includes(state.status)) {
        state.status='failed';state.error_code='process_lost';state.stop_confirmed=false;
        state.usage.usage_complete=false;state.usage.gap_reason='process_killed';
        this.save(state,row.digest);
      }
    }
  }
  get(id) {const r=this.db.prepare('SELECT * FROM runs WHERE id=?').get(id);if(!r)return null; const snapshot=JSON.parse(r.snapshot);
    if(snapshot.performance?.expires_at<=Date.now()){delete snapshot.performance;this.save(snapshot,r.digest);}
    return {digest:r.digest,snapshot};}
  save(snapshot,digest) {
    // Detailed performance belongs to the seven-day platform store.
    // Keep the native-history journal free of duplicate telemetry.
    snapshot={...snapshot};delete snapshot.performance;
    this.db.prepare('INSERT INTO runs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET digest=excluded.digest,snapshot=excluded.snapshot')
      .run(snapshot.run_id,digest,JSON.stringify(snapshot));
  }
  delete(id){this.db.prepare('DELETE FROM runs WHERE id=?').run(id);}
  close(){this.db.prepare('DELETE FROM owner WHERE token=?').run(this.token);this.db.close();}
}
