use std::{borrow::Cow, error::Error, path::Path};

use zvec_rust::{
    Collection, CollectionSchema, DataType, Doc, FieldSchema, Fts, IndexParams, MetricType,
    MultiQuery, SearchQuery, SubQuery,
};

use crate::domain::FTS_CONFIG;

use super::super::zvec;

const TEXT: &str = "text";
const EMBEDDING: &str = "embedding";
const FILE_ID: &str = "file_id";
const FILE_FIELDS: &[&str] = &[FILE_ID];
const RRF_K: i32 = 60;

pub(super) fn create(
    path: &Path,
    vector: Option<(u32, MetricType)>,
) -> Result<Collection, Box<dyn Error>> {
    zvec::initialize()?;
    let mut schema = CollectionSchema::new("benchmark_files")?;
    zvec::scalar(&mut schema, FILE_ID, DataType::Uint32, false, false)?;

    let mut text = FieldSchema::new(TEXT, DataType::String, false, 0)?;
    text.set_index_params(&IndexParams::fts(
        Some(FTS_CONFIG.tokenizer),
        Some(FTS_CONFIG.filters),
        None,
    )?)?;
    schema.add_field(&text)?;

    if let Some((dimension, metric)) = vector {
        let mut embedding = FieldSchema::new(EMBEDDING, DataType::VectorFp32, false, dimension)?;
        embedding.set_index_params(&IndexParams::flat(metric)?)?;
        schema.add_field(&embedding)?;
    }

    let path = path.to_str().ok_or("zvec collection path must be UTF-8")?;
    Ok(Collection::create_and_open(path, &schema, None)?)
}

pub(super) fn file_doc(id: u32, text: &str, vector: Option<&[f32]>) -> Result<Doc, Box<dyn Error>> {
    let mut doc = Doc::new()?;
    doc.try_set_pk(&format!("f{id}"))?;
    doc.add_u32(FILE_ID, id)?;
    doc.add_string(TEXT, &index_text(text))?;
    if let Some(vector) = vector {
        doc.add_vector_f32(EMBEDDING, vector)?;
    }
    Ok(doc)
}

pub(super) fn fts_query(
    text: &str,
    limit: i32,
    fields: &[&str],
) -> Result<SearchQuery, Box<dyn Error>> {
    let mut query = SearchQuery::fts(TEXT, &fts_payload(text)?, limit)?;
    query.set_output_fields(fields)?;
    query.set_include_vector(false)?;
    Ok(query)
}

pub(super) fn vector_query(vector: &[f32], limit: i32) -> Result<SearchQuery, Box<dyn Error>> {
    let mut query = SearchQuery::new(EMBEDDING, vector, limit)?;
    query.set_output_fields(FILE_FIELDS)?;
    query.set_include_vector(false)?;
    Ok(query)
}

pub(super) fn hybrid_query(
    text: &str,
    vector: &[f32],
    limit: i32,
    depth: i32,
) -> Result<MultiQuery, Box<dyn Error>> {
    let mut fts = SubQuery::new()?;
    fts.set_field_name(TEXT)?;
    fts.set_fts(&fts_payload(text)?)?;
    fts.set_num_candidates(depth)?;

    let mut semantic = SubQuery::new()?;
    semantic.set_field_name(EMBEDDING)?;
    semantic.set_query_vector(vector)?;
    semantic.set_num_candidates(depth)?;

    let mut query = MultiQuery::new()?;
    query.add_sub_query(&fts)?;
    query.add_sub_query(&semantic)?;
    query.set_rerank_rrf(RRF_K)?;
    query.set_topk(limit)?;
    query.set_output_fields(FILE_FIELDS)?;
    query.set_include_vector(false)?;
    Ok(query)
}

fn fts_payload(text: &str) -> zvec_rust::Result<Fts> {
    let mut fts = Fts::new()?;
    fts.set_match_string(&index_text(text.trim()))?;
    Ok(fts)
}

fn index_text(text: &str) -> Cow<'_, str> {
    if text.contains('\0') {
        Cow::Owned(text.replace('\0', " "))
    } else {
        Cow::Borrowed(text)
    }
}
