use anyhow::Result;
use skippy_api::serving::in_process::LocalModelOptions;
use skippy_production_consumer::{LoadEvidence, MODEL_ID, exercise_streams, open_runtime};

#[tokio::main]
async fn main() -> Result<()> {
    let (runtime, path) = open_runtime()?;
    let evidence = LoadEvidence::default();
    let observations = evidence.observe();
    let model = tokio::task::spawn_blocking(move || {
        LocalModelOptions::new("embedding-local", MODEL_ID, path)
            .with_limits(512, 1, 128)
            .with_gpu_layers(0)
            .with_observations(observations)
            .load(&runtime)
    })
    .await??;
    evidence.verify()?;
    let result = exercise_streams(&model).await;
    tokio::task::spawn_blocking(move || model.shutdown()).await?;
    result
}
