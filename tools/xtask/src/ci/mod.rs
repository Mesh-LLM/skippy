pub(crate) mod artifacts;
pub(crate) use crate::execution;
mod contracts;
pub(crate) mod matrix;
mod producers;
mod smoke;
use crate::command::DynResult;
use std::path::Path;

pub fn run(args: &[String]) -> DynResult<()> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["contracts"] => contracts::run(),
        ["host", id] => producers::host(id),
        ["runtime", id] => producers::runtime(id),
        ["package", id, host, runtime] => producers::package(id, Path::new(host), Path::new(runtime)),
        ["smoke", id, product] => smoke::run(id, Path::new(product)),
        ["model-fetch"] => smoke::prepare_model(),
        _ => Err("usage: ci {contracts|model-fetch|host ID|runtime ID|package ID HOST_DIR RUNTIME_DIR|smoke ID PRODUCT_DIR}".into()),
    }
}
