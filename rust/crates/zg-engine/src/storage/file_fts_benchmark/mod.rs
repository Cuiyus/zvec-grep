//! Native retrieval-only experiment over a frozen, already indexed corpus.

mod native;

use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fs,
    path::{Path, PathBuf},
    time::Instant,
};

use serde::{Deserialize, Serialize};
use zvec_rust::{Collection, CollectionOptions, Doc, MetricType};

use super::{files::Files, fragments::fragment_collection_name, zvec};
use crate::{
    domain::{
        Content, FileFormat,
        model::{Device, EmbeddingMetric, EmbeddingPurpose, ModelConfig},
    },
    models::{EmbeddingOptions, EmbeddingPrepareOptions, ModelRuntimeManager, ModelRuntimeRequest},
    utils::{decode_index_text, sha256_hex},
};

const LIMIT: i32 = 10;
const CANDIDATES: i32 = 200;
const REPETITIONS: u32 = 5;
type BenchResult<T> = Result<T, Box<dyn Error>>;

#[derive(Deserialize)]
struct Input {
    root: PathBuf,
    index: PathBuf,
    collection: PathBuf,
    model_cache: PathBuf,
    model: String,
    tasks: Vec<Task>,
    output: PathBuf,
    #[serde(default)]
    hybrid: bool,
}

#[derive(Deserialize)]
struct Task {
    id: String,
    query: String,
}

#[derive(Serialize)]
struct Output {
    indexed_files: usize,
    files: usize,
    fragments: u64,
    file_index_ms: f64,
    task_results: Vec<Observation>,
}

#[derive(Serialize)]
struct Observation {
    id: String,
    mode: &'static str,
    repetition: u32,
    elapsed_ms: f64,
    items: Vec<Item>,
}

#[derive(Serialize)]
struct Item {
    path: String,
    rank: usize,
    score: f32,
}

#[tokio::test]
#[ignore = "requires the pinned Retrieval-only corpus and an input manifest"]
async fn run() -> BenchResult<()> {
    let manifest = std::env::var_os("ZG_FILE_FTS_BENCH_INPUT")
        .ok_or("set ZG_FILE_FTS_BENCH_INPUT to the experiment input JSON")?;
    let input: Input = serde_json::from_slice(&fs::read(manifest)?)?;
    let root = input.root.canonicalize()?;
    if input.collection.exists() || input.output.exists() {
        return Err("experiment requires a new collection and output file".into());
    }
    for path in [&input.collection, &input.output] {
        if path
            .parent()
            .ok_or("expected an output parent")?
            .canonicalize()?
            .starts_with(&root)
        {
            return Err("experiment artifacts must be outside the searchable corpus".into());
        }
    }
    zvec::initialize()?;
    let (fragment_path, fragments) = open_fragments(&input.index)?;
    let (layout, query_vectors) = if input.hybrid {
        let (layout, vectors) = embed_queries(&input, &fragment_path).await?;
        (Some(layout), vectors)
    } else {
        (None, Vec::new())
    };

    let started_index = Instant::now();
    let (file_fragments, fragment_count) = collect_fragments(&fragments, layout)?;
    let files = native::create(&input.collection, layout)?;
    let (indexed_files, paths) = build_files(&input.index, &root, &files, file_fragments)?;
    files.flush()?;
    files.optimize()?;
    let file_index_ms = started_index.elapsed().as_secs_f64() * 1000.0;
    let mut observations = Vec::new();
    for (index, task) in input.tasks.iter().enumerate() {
        let fragment_fts = native::fts_query(&task.query, LIMIT, &["file_id"])?;
        let file_fts = native::fts_query(&task.query, LIMIT, &["file_id"])?;
        for repetition in 1..=REPETITIONS {
            observations.push(observe(task, "fragment_fts", repetition, &paths, || {
                fragments.query(&fragment_fts)
            })?);
            observations.push(observe(task, "file_fts", repetition, &paths, || {
                files.query(&file_fts)
            })?);
        }
        if let Some(vector) = query_vectors.get(index) {
            let file_vector = native::vector_query(vector, LIMIT)?;
            let file_hybrid = native::hybrid_query(&task.query, vector, LIMIT, CANDIDATES)?;
            for repetition in 1..=REPETITIONS {
                observations.push(observe(task, "file_vector", repetition, &paths, || {
                    files.query(&file_vector)
                })?);
                observations.push(observe(task, "file_hybrid", repetition, &paths, || {
                    files.multi_query(&file_hybrid)
                })?);
            }
        }
    }
    let output = Output {
        indexed_files,
        files: paths.len(),
        fragments: fragment_count,
        file_index_ms,
        task_results: observations,
    };
    fs::write(input.output, serde_json::to_vec_pretty(&output)?)?;
    Ok(())
}

async fn embed_queries(
    input: &Input,
    fragment_path: &Path,
) -> BenchResult<((u32, MetricType), Vec<Vec<f32>>)> {
    let manager = ModelRuntimeManager::new();
    let model = manager.acquire(ModelRuntimeRequest::new(
        &input.model,
        ModelConfig {
            device: Some(Device::Cpu),
            cache_dir: Some(input.model_cache.clone()),
            ..ModelConfig::default()
        },
        None,
    ))?;
    let info = model.info();
    if fragment_path.file_name().and_then(|name| name.to_str())
        != Some(fragment_collection_name(info).as_str())
    {
        return Err("query model differs from the fragment index model".into());
    }
    let dimension = u32::try_from(info.dimension())?;
    let metric = match info.metric() {
        EmbeddingMetric::Cosine => MetricType::Cosine,
        EmbeddingMetric::DotProduct => MetricType::Ip,
        EmbeddingMetric::Euclidean => MetricType::L2,
    };
    model
        .prepare(EmbeddingPrepareOptions::default(), None)
        .await?;
    let queries = input
        .tasks
        .iter()
        .map(|task| vec![Content::Text(task.query.clone())])
        .collect::<Vec<_>>();
    let vectors = model
        .embed(
            &queries,
            EmbeddingOptions {
                purpose: EmbeddingPurpose::Query,
                ..EmbeddingOptions::default()
            },
            None,
        )
        .await?
        .vectors;
    if vectors.len() != input.tasks.len() {
        return Err("embedding result count differs from the task count".into());
    }
    Ok(((dimension, metric), vectors))
}

fn open_fragments(index: &Path) -> BenchResult<(PathBuf, Collection)> {
    let partitions = fs::read_dir(index)?
        .map(|result| result.map(|entry| entry.path()))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .filter(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("fragments_"))
        })
        .collect::<Vec<_>>();
    let [path] = partitions.as_slice() else {
        return Err("experiment requires exactly one model partition".into());
    };
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    let path_str = path.to_str().ok_or("fragment index path must be UTF-8")?;
    Ok((path.clone(), Collection::open(path_str, Some(&options))?))
}

struct FileFragments {
    count: u32,
    sum: Vec<f64>,
}

impl FileFragments {
    #[allow(clippy::cast_possible_truncation)] // Convert pooled coordinates to the FP32 index type.
    fn mean(&self) -> Option<Vec<f32>> {
        (!self.sum.is_empty()).then(|| {
            self.sum
                .iter()
                .map(|value| (value / f64::from(self.count)) as f32)
                .collect()
        })
    }
}

fn collect_fragments(
    fragments: &Collection,
    layout: Option<(u32, MetricType)>,
) -> BenchResult<(BTreeMap<u32, FileFragments>, u64)> {
    let mut by_file = BTreeMap::new();
    let mut count = 0;
    for doc in fragments.iter_with_options(Some(&["file_id"]), layout.is_some())? {
        let doc = doc?;
        let file_id = zvec::u32_field(&doc, "file_id")?;
        let file = by_file.entry(file_id).or_insert_with(|| FileFragments {
            count: 0,
            sum: layout.map_or_else(Vec::new, |(dimension, _)| vec![0.0; dimension as usize]),
        });
        if layout.is_some() {
            let vector = zg_zvec_fp16::decode_embedding(&doc, "embedding")?
                .ok_or("missing indexed fragment vector")?;
            if vector.len() != file.sum.len() || vector.iter().any(|value| !value.is_finite()) {
                return Err("invalid indexed fragment vector".into());
            }
            for (sum, value) in file.sum.iter_mut().zip(vector) {
                *sum += f64::from(value);
            }
        }
        file.count += 1;
        count += 1;
    }
    if by_file.is_empty() {
        return Err("fragment index is empty".into());
    }
    Ok((by_file, count))
}

fn build_files(
    index: &Path,
    root: &Path,
    collection: &Collection,
    mut fragments: BTreeMap<u32, FileFragments>,
) -> BenchResult<(usize, BTreeMap<u32, String>)> {
    let mut indexed_files = 0;
    let mut paths = BTreeMap::new();
    let mut batch = Vec::new();
    for file in Files::open(index, true)?.list()? {
        if !file.index_status().is_indexed() {
            continue;
        }
        indexed_files += 1;
        let file_id = file.id().get();
        let Some(file_fragments) = fragments.remove(&file_id) else {
            continue;
        };
        let path = file.relative_path();
        let abs = root.join(path).canonicalize()?;
        if !abs.starts_with(root) {
            return Err(format!("indexed source escapes the corpus: {}", path.display()).into());
        }
        let bytes = fs::read(&abs)?;
        if file.snapshot().content_hash.as_deref() != Some(sha256_hex(&bytes).as_str()) {
            return Err(format!("indexed source hash changed: {}", path.display()).into());
        }
        let formats = FileFormat::from_path(&abs)?;
        let source = decode_index_text(&formats, &bytes)
            .ok_or_else(|| format!("cannot decode indexed source: {}", path.display()))?;
        let vector = file_fragments.mean();
        batch.push(native::file_doc(file_id, &source, vector.as_deref())?);
        paths.insert(
            file_id,
            path.to_str().ok_or("source path must be UTF-8")?.to_owned(),
        );
        if batch.len() == zvec::WRITE_BATCH {
            zvec::write_docs(collection, &batch, "write benchmark files")?;
            batch.clear();
        }
    }
    if !fragments.is_empty() {
        return Err("fragment refers to an unknown source file".into());
    }
    zvec::write_docs(collection, &batch, "write benchmark files")?;
    Ok((indexed_files, paths))
}

fn observe(
    task: &Task,
    mode: &'static str,
    repetition: u32,
    paths: &BTreeMap<u32, String>,
    query: impl FnOnce() -> zvec_rust::Result<Vec<Doc>>,
) -> BenchResult<Observation> {
    let start = Instant::now();
    let docs = query().map_err(|error| format!("{} {mode}: {error}", task.id))?;
    let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;
    let mut unique = BTreeSet::new();
    let mut items = Vec::with_capacity(docs.len());
    for (index, doc) in docs.iter().enumerate() {
        let file_id = zvec::u32_field(doc, "file_id")?;
        let path = paths
            .get(&file_id)
            .ok_or("result file ID missing from corpus")?;
        if mode != "fragment_fts" && !unique.insert(file_id) {
            return Err(format!("{mode} returned duplicate file {path}").into());
        }
        items.push(Item {
            path: path.clone(),
            rank: index + 1,
            score: doc.get_score(),
        });
    }
    Ok(Observation {
        id: task.id.clone(),
        mode,
        repetition,
        elapsed_ms,
        items,
    })
}
