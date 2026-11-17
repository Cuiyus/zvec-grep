//! Post-benchmark read-only audit. Public MCP outputs remain the quality evidence.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, HashMap}, fs::{self, File}, io::{BufWriter, Write}, path::Path};
use zvec_rust::*;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn embedding_bytes(doc: &Doc) -> Result<Vec<u8>> {
    let mut ptr = std::ptr::null();
    let mut size = 0;
    // The collection stores FP16; hash those exact bytes, without FP32 conversion.
    let code = unsafe { sys::zvec_doc_get_field_value_pointer(doc.as_raw(), b"embedding\0".as_ptr().cast(),
        DataType::VectorFp16 as u32, &mut ptr, &mut size) };
    if code != sys::ZVEC_OK || ptr.is_null() || size == 0 || size % 2 != 0 {
        return Err(format!("missing/invalid stored FP16 vector: code={code}, bytes={size}").into());
    }
    Ok(unsafe { std::slice::from_raw_parts(ptr.cast::<u8>(), size) }.to_vec())
}

fn write_json(path: &Path, value: &Value) -> Result<()> {
    let mut file = BufWriter::new(File::create(path)?);
    serde_json::to_writer(&mut file, value)?; file.write_all(b"\n")?; Ok(())
}

fn main() -> Result<()> {
    let spec: Value = serde_json::from_slice(&fs::read(std::env::args().nth(1).ok_or("spec.json required")?)?)?;
    initialize(Some(&ConfigBuilder::new().enable_console_log(false).num_threads(2)))?;
    let out = Path::new(spec["output"].as_str().ok_or("output required")?);
    fs::create_dir_all(out)?;
    let mut options = CollectionOptions::new()?; options.set_read_only(true)?;
    let index = Collection::open(spec["collection"].as_str().ok_or("collection required")?,Some(&options))?;
    let mut records = BTreeMap::new();
    let mut owners = HashMap::new();
    for item in index.iter_with_options(Some(&["document_id","entity_id","text"]),true)? {
        let doc = item?;
        let id = doc.get_string("document_id")?.ok_or("missing ID")?;
        assert_eq!(Some(id.as_str()),doc.get_pk());
        let owner = doc.get_string("entity_id")?.ok_or("missing owner")?;
        let text = doc.get_string("text")?.ok_or("missing text")?;
        let vector = embedding_bytes(&doc)?;
        let vector_hash = format!("{:x}",Sha256::digest(&vector));
        assert!(owners.insert(id.clone(),owner.clone()).is_none());
        records.insert(id.clone(),json!({"id":id,"entity_id":owner,"text":text,
            "embedding_sha256":vector_hash,"embedding_bytes":vector.len()}));
    }
    assert!(!records.is_empty());
    let mut dump=BufWriter::new(File::create(out.join("fragments.jsonl"))?);
    let mut texts=Sha256::new(); let mut vectors=Sha256::new();
    for (id,row) in &records {
        serde_json::to_writer(&mut dump,row)?;dump.write_all(b"\n")?;
        texts.update(serde_json::to_vec(&json!([id,row["entity_id"],row["text"]]))?);texts.update(b"\n");
        vectors.update(serde_json::to_vec(&json!([id,row["embedding_sha256"],row["embedding_bytes"]]))?);vectors.update(b"\n");
    }
    dump.flush()?;
    let mut queries=Vec::new();
    for q in spec["queries"].as_array().ok_or("queries required")? {
        let text=q["text"].as_str().ok_or("query text required")?;
        let mut fts=Fts::new()?;fts.set_match_string(&text.replace('\0'," "))?;
        let mut request=SearchQuery::fts("text",&fts,200)?;request.set_output_fields(&["document_id"])?;
        let mut hits=index.query(&request)?.into_iter().map(|d|Ok((d.get_string("document_id")?.ok_or("missing hit ID")?,d.get_score())))
            .collect::<Result<Vec<_>>>()?;
        hits.sort_by(|a,b|b.1.total_cmp(&a.1).then_with(||a.0.cmp(&b.0)));
        let top:Vec<Value>=hits.iter().map(|(id,score)|json!([id,owners.get(id).expect("unknown owner"),score])).collect();
        queries.push(json!({"id":q["id"],"text":text,"top200":top}));
    }
    write_json(&out.join("native.json"),&json!({"queries":queries}))?;
    write_json(&out.join("manifest.json"),&json!({"native_version":version(),"spec":spec,
        "fragments":records.len(),"entities":owners.values().collect::<std::collections::HashSet<_>>().len(),
        "text_identity_sha256":format!("{:x}",texts.finalize()),"vector_identity_sha256":format!("{:x}",vectors.finalize()),
        "quality_source":"unchanged public MCP benchmark","candidate_budget":200}))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn audit_reads_the_stored_fp16_bytes() -> Result<()> {
        initialize(Some(&ConfigBuilder::new().enable_console_log(false)))?;
        let path=std::env::temp_dir().join(format!("fts-full-audit-{}-{}",std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_nanos()));
        let schema=CollectionSchema::builder("audit_fixture")
            .add_field(FieldSchema::new("document_id",DataType::String,false,0)?)
            .add_vector_field("embedding",DataType::VectorFp16,2,IndexParams::hnsw(MetricType::Cosine,16,200)?)
            .build()?;
        let index=Collection::create_and_open(path.to_str().unwrap(),&schema,None)?;
        let mut doc=Doc::new()?;doc.set_pk("one");doc.add_string("document_id","one")?;
        let stored=[0u8,60,0,0];
        assert_eq!(unsafe { sys::zvec_doc_add_field_by_value(doc.as_raw(),b"embedding\0".as_ptr().cast(),
            DataType::VectorFp16 as u32,stored.as_ptr().cast(),stored.len()) },sys::ZVEC_OK);
        assert_eq!(index.insert(&[&doc])?.success_count,1);index.flush()?;
        let docs=index.iter_with_options(Some(&["document_id"]),true)?.collect::<zvec_rust::Result<Vec<_>>>()?;
        assert_eq!(docs.len(),1);assert_eq!(embedding_bytes(&docs[0])?,[0,60,0,0]);
        drop(docs);drop(index);fs::remove_dir_all(path)?;Ok(())
    }
}
