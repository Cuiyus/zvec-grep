use std::error::Error;
use zvec_rust::{Collection, CollectionOptions, ConfigBuilder};

fn main() -> Result<(), Box<dyn Error>> {
    let config = ConfigBuilder::new()
        .num_threads(2)
        .memory_limit(512 * 1024 * 1024)
        .enable_console_log(false)
        .build();
    zvec_rust::initialize(Some(&config))?;
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    for path in std::env::args().skip(1) {
        let collection = Collection::open(&path, Some(&options))?;
        let count = collection.stats()?.doc_count;
        collection.close()?;
        println!("ZG_COUNT:{count}");
    }
    Ok(())
}
