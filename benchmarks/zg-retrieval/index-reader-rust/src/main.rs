use serde_json::{json, Map, Value};
use std::error::Error;
use zvec_rust::{sys, Collection, CollectionOptions, DataType};

struct NativeSchema(*mut sys::zvec_collection_schema_t);

impl Drop for NativeSchema {
    fn drop(&mut self) {
        unsafe { sys::zvec_collection_schema_destroy(self.0) };
    }
}

fn vector_schema(collection: &Collection) -> Result<Value, Box<dyn Error>> {
    let mut schema = std::ptr::null_mut();
    // The C API returns an owned schema copy. Field and index pointers borrow it.
    let code = unsafe { sys::zvec_collection_get_schema(collection.as_raw(), &mut schema) };
    if code != 0 || schema.is_null() {
        return Err(format!("cannot inspect native schema: {code}").into());
    }
    let schema = NativeSchema(schema);
    let field = unsafe { sys::zvec_collection_schema_get_field(schema.0, c"embedding".as_ptr()) };
    if field.is_null() {
        return Err("fragment schema has no embedding field".into());
    }
    let params = unsafe { sys::zvec_field_schema_get_index_params(field) };
    if params.is_null() {
        return Err("embedding field has no index parameters".into());
    }
    let value = unsafe {
        json!({
            "name": "embedding",
            "dimension": sys::zvec_field_schema_get_dimension(field),
            "data_type": format!("{:?}", DataType::from(sys::zvec_field_schema_get_data_type(field))),
            "index_params": {
                "index_type": sys::zvec_index_params_get_type(params),
                "metric_type": sys::zvec_index_params_get_metric_type(params),
                "m": sys::zvec_index_params_get_hnsw_m(params),
                "ef_construction": sys::zvec_index_params_get_hnsw_ef_construction(params),
            },
            "parameter_source": "persisted collection schema via native C API",
        })
    };
    Ok(value)
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 3 {
        return Err("requires COPY_PATH and COLLECTION_ROLE".into());
    }
    let config = zvec_rust::ConfigBuilder::new()
        .num_threads(2)
        .memory_limit(512 * 1024 * 1024)
        .enable_console_log(false)
        .build();
    zvec_rust::initialize(Some(&config))?;
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    let collection = Collection::open(&args[1], Some(&options))?;
    let stats = collection.stats()?;
    let mut indexes = Map::new();
    for index in stats.indexes {
        indexes.insert(index.name, json!(index.completeness));
    }
    let mut files = Vec::new();
    if args[2] == "files" {
        for result in collection.iter_with_options(Some(&["payload", "relative_path"]), false)? {
            let doc = result?;
            let payload = doc
                .get_string("payload")?
                .ok_or("file row has no payload")?;
            let parsed: Value = serde_json::from_str(&payload)?;
            files.push(json!({
                "id": parsed["id"],
                "relative_path": doc.get_string("relative_path")?,
                "size_bytes": parsed["snapshot"]["size_bytes"],
                "entity_count": parsed["index_status"]["entity_count"].as_u64().unwrap_or(0),
                "indexed": parsed["index_status"]["kind"] == "indexed",
            }));
        }
        if files.len() as u64 != stats.doc_count {
            return Err("file iterator/count mismatch".into());
        }
    }
    let vectors = if args[2] == "fragments" {
        vec![vector_schema(&collection)?]
    } else {
        vec![]
    };
    let output = json!({"schema_name": collection.schema()?.name(), "vectors": vectors,
        "doc_count": stats.doc_count, "index_completeness": indexes, "files": files,
        "reader": "zvec-rust@0.7.2, linked to the product package's libzvec_c_api.so"});
    collection.close()?;
    println!("ZG_INDEX_STATS_JSON:{output}");
    Ok(())
}
