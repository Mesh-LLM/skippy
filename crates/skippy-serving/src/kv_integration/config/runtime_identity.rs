//! Construct cache tiers from the loaded runtime's admitted capabilities.
use super::*;

impl KvStageIntegration {
    /// Combine loaded cache capabilities with the model identity admitted by serving.
    pub fn from_runtime_with_l3_manager(
        config: &StageConfig,
        runtime: &Arc<Mutex<crate::runtime_state::RuntimeState>>,
        manager: Option<L3CacheManager>,
        observer: Option<Arc<dyn KvLifecycleObserver>>,
    ) -> Result<Option<Self>> {
        let (kind, memory) = {
            let runtime = crate::runtime_state::panic_recovery::lock_runtime(runtime);
            let capability = runtime.model.capability();
            (
                capability.as_ref().map(|value| value.state_kind),
                capability.as_ref().map(|value| value.cache_capabilities),
            )
        };
        Self::from_loaded_model_with_l3_manager(config, kind, memory, manager, observer)
    }
}
