//! Offline FTS experiments on frozen physical fragments. Never opens source indexes writable.
use serde_json::{json, Value};
use std::{collections::HashMap, fs::{self, File}, io::{BufWriter, Write}, path::Path, time::Instant};
use zvec_rust::*;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn write_json(path: &Path, value: &Value) -> Result<()> {
    let mut file = BufWriter::new(File::create(path)?);
    serde_json::to_writer(&mut file, value)?;
    file.write_all(b"\n")?;
    Ok(())
}

fn query(index: &Collection, text: &str, topk: usize) -> Result<Vec<(String, f32)>> {
    let mut fts = Fts::new()?;
    fts.set_match_string(&text.replace('\0', " "))?;
    let mut req = SearchQuery::fts("text", &fts, topk.try_into()?)?;
    req.set_output_fields(&["document_id"])?;
    let mut hits = index.query(&req)?.into_iter().map(|doc| {
        Ok((doc.get_string("document_id")?.ok_or("missing document_id")?, doc.get_score()))
    }).collect::<Result<Vec<_>>>()?;
    // Identical to PR417's single-index FTS tie ordering after native top-K.
    hits.sort_by(|a,b| b.1.total_cmp(&a.1).then_with(||a.0.cmp(&b.0)));
    Ok(hits)
}

fn measure(index: &Collection, queries: &[Value], count: usize, out: &Path,
           owners: &HashMap<String, String>) -> Result<Value> {
    let mut records = Vec::new();
    for q in queries {
        let text = q["text"].as_str().ok_or("query text missing")?;
        let mut timing = Vec::new();
        let mut top = Vec::new();
        for repetition in 0..5 {
            let start = Instant::now();
            let hits = query(index, text, 200)?;
            timing.push(start.elapsed().as_micros());
            if repetition == 0 { top = hits; } else { assert_eq!(top, hits, "unstable top200"); }
        }
        let depth = count.min(1_000_000).max(1);
        let deep = query(index, text, depth)?;
        let enrich = |hits: &[(String,f32)]| -> Vec<Value> {
            hits.iter().map(|(id,score)|json!([id,owners.get(id).expect("unknown fragment"),score])).collect()
        };
        records.push(json!({"id":q["id"],"text":text,"top200":enrich(&top),"deep":enrich(&deep),
                            "deep_limit":depth,"deep_may_be_capped":deep.len()==depth && count>depth,
                            "top200_us":timing,"stable_five_repetitions":true}));
    }
    let result = json!({"queries":records});
    write_json(out, &result)?;
    Ok(result)
}

fn bytes(path: &Path) -> Result<u64> {
    let mut total = 0;
    for entry in fs::read_dir(path)? {
        let p = entry?.path();
        total += if p.is_dir() { bytes(&p)? } else { fs::metadata(p)?.len() };
    }
    Ok(total)
}

fn main() -> Result<()> {
    let spec_path = std::env::args().nth(1).ok_or("usage: fts-analysis spec.json")?;
    let spec: Value = serde_json::from_slice(&fs::read(spec_path)?)?;
    initialize(Some(&ConfigBuilder::new().enable_console_log(false).num_threads(2)))?;
    let output = Path::new(spec["output"].as_str().ok_or("output required")?);
    fs::create_dir_all(output)?;
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    let original = Collection::open(spec["collection"].as_str().ok_or("collection required")?, Some(&options))?;
    let mut data = Vec::new();
    let mut owners = HashMap::new();
    let mut dump = BufWriter::new(File::create(output.join("fragments.jsonl"))?);
    for item in original.iter_with_options(Some(&["document_id","entity_id","text"]), false)? {
        let doc = item?;
        let id = doc.get_string("document_id")?.ok_or("missing fragment ID")?;
        assert_eq!(Some(id.as_str()), doc.get_pk());
        let entity = doc.get_string("entity_id")?.ok_or("missing entity ID")?;
        let text = doc.get_string("text")?.ok_or("missing FTS text")?;
        assert!(owners.insert(id.clone(), entity.clone()).is_none(), "duplicate fragment");
        serde_json::to_writer(&mut dump, &json!({"id":id,"entity_id":entity,"text":text}))?;
        dump.write_all(b"\n")?;
        data.push((id,entity,text));
    }
    dump.flush()?;
    assert!(!data.is_empty(), "empty fragment collection");
    let queries = spec["queries"].as_array().ok_or("queries required")?;
    let original_results = measure(&original,queries,data.len(),&output.join("original.json"),&owners)?;
    drop(original);
    let mut summaries = Vec::new();
    // Configurations fixed before seeing new benchmark outcomes: a 2x2 comparison.
    for (name, tokenizer, stem) in [("jieba_lower","jieba",false),("jieba_stem","jieba",true),
                                  ("standard_lower","standard",false),("standard_stem","standard",true)] {
        let dir = output.join(format!("index_{name}"));
        let filters = if stem { vec!["lowercase","stemmer"] } else { vec!["lowercase"] };
        let params = IndexParams::fts(Some(tokenizer),Some(&filters),Some("{\"stemmer_lang\":\"english\"}"))?;
        let schema = CollectionSchema::builder(name)
            .add_field(FieldSchema::new("document_id",DataType::String,false,0)?)
            .add_field(FieldSchema::new("entity_id",DataType::String,false,0)?)
            .add_indexed_field("text",DataType::String,params).build()?;
        let started = Instant::now();
        let index = Collection::create_and_open(dir.to_str().ok_or("path UTF8")?, &schema, None)?;
        for batch in data.chunks(256) {
            let docs = batch.iter().map(|(id,entity,text)| -> Result<Doc> {
                let mut d = Doc::new()?;
                d.set_pk(id);
                d.add_string("document_id",id)?;
                d.add_string("entity_id",entity)?;
                d.add_string("text",text)?;
                Ok(d)
            }).collect::<Result<Vec<_>>>()?;
            let refs: Vec<&Doc> = docs.iter().collect();
            let result = index.insert(&refs)?;
            assert_eq!(result.success_count as usize,docs.len());
        }
        index.flush()?;
        index.optimize()?;
        let build_ms = started.elapsed().as_millis();
        let result = measure(&index, queries,data.len(),&output.join(format!("{name}.json")),&owners)?;
        drop(index);
        let index_bytes = bytes(&dir)?;
        // Capture equivalence; don't silently substitute a failed reconstruction for the baseline.
        let equivalence = if name == "jieba_lower" {
            Some(queries.iter().enumerate().map(|(i,q)| {
                let original = &original_results["queries"][i];
                let rebuilt = &result["queries"][i];
                json!({"id":q["id"],"top200_identical":original["top200"]==rebuilt["top200"],
                       "deep_identical":original["deep"]==rebuilt["deep"]})
            }).collect::<Vec<_>>())
        } else { None };
        summaries.push(json!({"name":name,"tokenizer":tokenizer,"filters":filters,
                             "build_ms":build_ms,"index_bytes":index_bytes,"equivalence":equivalence}));
        // Only this program's derived indexes are removed; the original remains read-only.
        fs::remove_dir_all(&dir)?;
        eprintln!("completed {name}: {} fragments", data.len());
    }
    write_json(&output.join("manifest.json"), &json!({"native_version":version(),"spec":spec,
        "fragments":data.len(),"entities":owners.values().collect::<std::collections::HashSet<_>>().len(),
        "configs":summaries,"quality_budget_fragments":200,"vector_recomputed":false}))?;
    Ok(())
}
