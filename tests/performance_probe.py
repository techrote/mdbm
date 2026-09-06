"""Development benchmark, not runtime code. No pass/fail claim about terminal latency."""
import argparse,gc,importlib.metadata,json,os,platform,statistics,subprocess,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mdbm as m
from render_support import scene,THEMES

def ms(start):return (time.perf_counter()-start)*1000

def percentile(values,p):return sorted(values)[max(0,min(len(values)-1,int((len(values)-1)*p)))]

def large():
    import resource
    start=time.perf_counter()
    text=''.join(f'line {i:06d}: sample Markdown source\n' for i in range(100000))
    doc=m.DocumentModel(text)
    construction=ms(start)
    edits=[]
    for offset in (0,len(text)//2,len(text)):
        doc.move_to(offset);start=time.perf_counter();doc.insert('X');edits.append(ms(start));doc.undo()
    start=time.perf_counter();parsed,preview=m.parse_and_layout(doc.text,doc.generation,100,False)
    parse_ms=ms(start)
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mib=rss/(1024*1024 if sys.platform=='darwin' else 1024)
    return {'source_lines':100000,'indexed_lines':doc.line_count,'utf8_bytes':len(text.encode()),
            'line_index_bytes':len(doc._line_starts)*doc._line_starts.itemsize,
            'construct_ms':round(construction,3),'edit_ms':[round(x,3) for x in edits],
            'parse_and_layout_ms':round(parse_ms,3),'preview_rows':len(preview.rows),
            'process_peak_rss_mib':round(mib,3),'undo_steps_retained':len(doc.undo_stack),
            'limitation':'One isolated POSIX process. Peak RSS includes interpreter and dependencies; this is not incremental parsing or an adversarial document bound.'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--large-worker',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.large_worker:
        print(json.dumps(large()));return
    result={'schema':'mdbm-performance/1.0','python':sys.version.split()[0],
      'platform':platform.platform(),'logical_cpus':os.cpu_count(),'dependencies':{},
      'method':'160x50 owned-cell composition, fixed seed, normal motion, 5 warm-ups + 60 measured frames. No physical terminal, input transport or parser scheduling latency is included.',
      'composition':{}}
    for name in ('prompt-toolkit','markdown-it-py','mdit-py-plugins','Pygments','wcwidth','regex','Send2Trash'):
        try:result['dependencies'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:result['dependencies'][name]='MISSING'
    with tempfile.TemporaryDirectory() as tmp:
        for theme in ('minimal',*THEMES):
            w,_=scene(Path(tmp),theme,'truecolour',160,50)
            for _ in range(5):w.frame(160,50)
            samples=[]
            for _ in range(60):
                start=time.perf_counter();w.frame(160,50);samples.append(ms(start))
            unchanged=w.last_damage
            w.doc.insert('x');start=time.perf_counter();w.frame(160,50);one=ms(start)
            result['composition'][theme]={'samples':len(samples),'p50_ms':round(statistics.median(samples),3),
              'p95_ms':round(percentile(samples,.95),3),'max_ms':round(max(samples),3),
              'unchanged_frame_damage':unchanged,'single_insert_frame_ms':round(one,3),'single_insert_changed_cells':w.last_damage}
    if os.name=='posix':
        completed=subprocess.run([sys.executable,__file__,'--large-worker'],capture_output=True,text=True,timeout=180)
        if completed.returncode==0:result['large_document']=json.loads(completed.stdout)
        else:result['large_document']={'error':completed.stderr[-4000:]}
    else:result['large_document']={'not_run':'resource peak-RSS probe is POSIX-only.'}
    rendered=json.dumps(result,indent=2)+'\n'
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(rendered)
    print(rendered)

if __name__=='__main__':main()
