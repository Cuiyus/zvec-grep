use serde_json::{json, Map, Value};
use std::error::Error;
use zvec_rust::{Collection, CollectionOptions};

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 3 { return Err("requires COPY_PATH and COLLECTION_ROLE".into()); }
    let config = zvec_rust::ConfigBuilder::new().num_threads(2)
        .memory_limit(512 * 1024 * 1024).enable_console_log(false).build();
    zvec_rust::initialize(Some(&config))?;
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    let collection = Collection::open(&args[1], Some(&options))?;
    let stats = collection.stats()?;
    let mut indexes = Map::new();
    for index in stats.indexes { indexes.insert(index.name, json!(index.completeness)); }
    let mut files = Vec::new();
    if args[2] == "files" {
        for result in collection.iter_with_options(Some(&["payload", "relative_path"]), false)? {
            let doc = result?;
            let payload = doc.get_string("payload")?.ok_or("file row has no payload")?;
            let parsed: Value = serde_json::from_str(&payload)?;
            files.push(json!({
                "id": parsed["id"],
                "relative_path": doc.get_string("relative_path")?,
                "size_bytes": parsed["snapshot"]["size_bytes"],
                "entity_count": parsed["index_status"]["entity_count"].as_u64().unwrap_or(0),
                "indexed": parsed["index_status"]["kind"] == "indexed",
            }));
        }
        if files.len() as u64 != stats.doc_count { return Err("file iterator/count mismatch".into()); }
    }
    let output = json!({"schema_name": collection.schema()?.name(),
        "doc_count": stats.doc_count, "index_completeness": indexes, "files": files,
        "reader": "zvec-rust@0.7.2, linked to the product package's libzvec_c_api.so"});
    collection.close()?;
    println!("ZG_INDEX_STATS_JSON:{output}");
    Ok(())
}
