//! AMD GPU memory as the amdgpu driver counts it. An APU such as Strix Halo
//! gives its GPU a BIOS carve-out of VRAM plus GTT, system memory mapped for
//! the GPU, and the HIP runtime's free and total cover only the carve-out, so
//! the device gauges miss most of what a model holds there. The driver counts
//! both regions: per card in sysfs, for every process, and per process in
//! the fdinfo of each DRM client it opened.
//!
//! Elsewhere the paths do not exist and every reading is empty; the process
//! is only scanned when an amdgpu card was found.

use std::{collections::BTreeMap, fs, path::Path};

use opentelemetry_proto::tonic::metrics::v1::Metric;
use serde_json::{Value, json};

use super::otlp_metrics::{gauge, point};

const DRM_CLASS: &str = "/sys/class/drm";

/// A memory region the amdgpu driver accounts separately.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(super) enum Region {
    Vram,
    Gtt,
}

impl Region {
    const ALL: [Self; 2] = [Self::Vram, Self::Gtt];

    fn name(self) -> &'static str {
        match self {
            Self::Vram => "vram",
            Self::Gtt => "gtt",
        }
    }
}

/// One card's use of one region, by every process.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct CardRegion {
    card: String,
    region: Region,
    used_bytes: u64,
    total_bytes: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(super) struct AmdgpuMemory {
    cards: Vec<CardRegion>,
    /// This process's resident bytes per region, over all its amdgpu clients.
    process: BTreeMap<Region, u64>,
}

impl AmdgpuMemory {
    pub(super) fn now() -> Self {
        let cards = cards(Path::new(DRM_CLASS));
        let process = if cards.is_empty() {
            BTreeMap::new()
        } else {
            process_regions(own_drm_fdinfo())
        };
        Self { cards, process }
    }
}

fn cards(drm_class: &Path) -> Vec<CardRegion> {
    let Ok(entries) = fs::read_dir(drm_class) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| is_card(name))
        .collect();
    names.sort();
    names
        .into_iter()
        .flat_map(|card| {
            let device = drm_class.join(&card).join("device");
            Region::ALL.into_iter().filter_map(move |region| {
                let read = |what: &str| {
                    let path = device.join(format!("mem_info_{}_{what}", region.name()));
                    fs::read_to_string(path).ok()?.trim().parse::<u64>().ok()
                };
                Some(CardRegion {
                    card: card.clone(),
                    region,
                    used_bytes: read("used")?,
                    total_bytes: read("total")?,
                })
            })
        })
        .collect()
}

/// `card0`, not a connector such as `card0-DP-1` or a render node.
fn is_card(name: &str) -> bool {
    name.strip_prefix("card")
        .is_some_and(|index| !index.is_empty() && index.bytes().all(|byte| byte.is_ascii_digit()))
}

/// The fdinfo of every descriptor this process holds on a DRM device.
fn own_drm_fdinfo() -> Vec<String> {
    let Ok(fds) = fs::read_dir("/proc/self/fd") else {
        return Vec::new();
    };
    fds.flatten()
        .filter(|fd| fs::read_link(fd.path()).is_ok_and(|target| target.starts_with("/dev/dri/")))
        .filter_map(|fd| {
            fs::read_to_string(Path::new("/proc/self/fdinfo").join(fd.file_name())).ok()
        })
        .collect()
}

/// Each amdgpu client counted once: a duplicated descriptor repeats its
/// client's id and its memory.
fn process_regions(fdinfo: impl IntoIterator<Item = String>) -> BTreeMap<Region, u64> {
    let clients: BTreeMap<u64, BTreeMap<Region, u64>> = fdinfo
        .into_iter()
        .filter_map(|text| amdgpu_client(&text))
        .collect();
    clients
        .into_values()
        .flatten()
        .fold(BTreeMap::new(), |mut sum, (region, bytes)| {
            *sum.entry(region).or_default() += bytes;
            sum
        })
}

/// The client id and resident bytes per region of one DRM fdinfo, or None
/// when another driver owns the descriptor.
fn amdgpu_client(fdinfo: &str) -> Option<(u64, BTreeMap<Region, u64>)> {
    let fields: BTreeMap<&str, &str> = fdinfo
        .lines()
        .filter_map(|line| line.split_once(':'))
        .map(|(key, value)| (key.trim(), value.trim()))
        .collect();
    if *fields.get("drm-driver")? != "amdgpu" {
        return None;
    }
    let client = fields.get("drm-client-id")?.parse::<u64>().ok()?;
    let regions = Region::ALL
        .into_iter()
        .filter_map(|region| {
            // drm-resident-<region> is the common DRM key; older kernels give amdgpu's
            // drm-memory-<region>
            let value = fields
                .get(format!("drm-resident-{}", region.name()).as_str())
                .or_else(|| fields.get(format!("drm-memory-{}", region.name()).as_str()))?;
            Some((region, fdinfo_bytes(value)?))
        })
        .collect();
    Some((client, regions))
}

/// A DRM fdinfo memory value: a count with an optional `KiB` or `MiB` unit.
fn fdinfo_bytes(value: &str) -> Option<u64> {
    let mut parts = value.split_whitespace();
    let count: u64 = parts.next()?.parse().ok()?;
    let unit: u64 = match parts.next() {
        None => 1,
        Some("KiB") => 1 << 10,
        Some("MiB") => 1 << 20,
        Some(_) => return None,
    };
    count.checked_mul(unit)
}

pub(super) fn gauges(metrics: &mut Vec<Metric>, memory: &AmdgpuMemory, time: u64) {
    if !memory.cards.is_empty() {
        let by_card = |value: fn(&CardRegion) -> u64| {
            memory
                .cards
                .iter()
                .map(|card| {
                    point(
                        time,
                        value(card) as f64,
                        &[("card", card.card.as_str()), ("region", card.region.name())],
                    )
                })
                .collect()
        };
        metrics.push(gauge(
            "skippy.gpu.amdgpu.memory.usage",
            "By",
            "Memory the amdgpu driver counts as used on the card, by every process",
            by_card(|card| card.used_bytes),
        ));
        metrics.push(gauge(
            "skippy.gpu.amdgpu.memory.limit",
            "By",
            "The card's VRAM carve-out, or its GTT size",
            by_card(|card| card.total_bytes),
        ));
    }
    if !memory.process.is_empty() {
        metrics.push(gauge(
            "skippy.process.gpu_memory.usage",
            "By",
            "Memory this process holds resident on amdgpu GPUs",
            memory
                .process
                .iter()
                .map(|(region, bytes)| point(time, *bytes as f64, &[("region", region.name())]))
                .collect(),
        ));
    }
}

pub(super) fn snapshot(attrs: &mut BTreeMap<String, Value>, key: impl Fn(&str) -> String) {
    let memory = AmdgpuMemory::now();
    for card in &memory.cards {
        let name = format!("gpu.amdgpu.{}.{}", card.card, card.region.name());
        attrs.insert(key(&format!("{name}_used_bytes")), json!(card.used_bytes));
        attrs.insert(key(&format!("{name}_total_bytes")), json!(card.total_bytes));
    }
    for (region, bytes) in &memory.process {
        attrs.insert(
            key(&format!("process.gpu_{}_bytes", region.name())),
            json!(bytes),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const AMDGPU: &str = "pos:\t0\nflags:\t02100002\ndrm-driver:\tamdgpu\ndrm-client-id:\t7\n\
                          drm-memory-vram:\t1024 KiB\ndrm-memory-gtt:\t2 MiB\n";

    #[test]
    fn amdgpu_fdinfo_gives_resident_bytes_per_region() {
        let (client, regions) = amdgpu_client(AMDGPU).expect("an amdgpu client");
        assert_eq!(client, 7);
        assert_eq!(
            regions,
            BTreeMap::from([(Region::Vram, 1 << 20), (Region::Gtt, 2 << 20)])
        );
    }

    #[test]
    fn the_common_drm_key_wins_over_amdgpus_own() {
        let both = format!("{AMDGPU}drm-resident-vram:\t8 KiB\n");
        let (_, regions) = amdgpu_client(&both).expect("an amdgpu client");
        assert_eq!(regions.get(&Region::Vram), Some(&(8 << 10)));
    }

    #[test]
    fn a_client_counts_once_and_other_drivers_not_at_all() {
        let other = "drm-driver:\ti915\ndrm-client-id:\t9\ndrm-memory-vram:\t4 MiB\n".to_string();
        let total = process_regions([AMDGPU.to_string(), AMDGPU.to_string(), other]);
        assert_eq!(
            total,
            BTreeMap::from([(Region::Vram, 1 << 20), (Region::Gtt, 2 << 20)])
        );
    }

    #[test]
    fn units_are_read_and_others_refused() {
        assert_eq!(fdinfo_bytes("5"), Some(5));
        assert_eq!(fdinfo_bytes("5 KiB"), Some(5 << 10));
        assert_eq!(fdinfo_bytes("5 GiB"), None);
    }

    #[test]
    fn cards_are_read_from_sysfs_and_connectors_skipped() {
        let root = tempfile::tempdir().expect("a temporary directory");
        let device = root.path().join("card1").join("device");
        fs::create_dir_all(&device).expect("a card");
        fs::create_dir_all(root.path().join("card1-DP-1")).expect("a connector");
        for (file, value) in [
            ("mem_info_vram_used", "512"),
            ("mem_info_vram_total", "1024\n"),
            ("mem_info_gtt_used", "3"),
            ("mem_info_gtt_total", "4"),
        ] {
            fs::write(device.join(file), value).expect("a counter");
        }
        let found = cards(root.path());
        assert_eq!(found.len(), 2);
        assert_eq!(
            (found[0].region, found[0].used_bytes, found[0].total_bytes),
            (Region::Vram, 512, 1024)
        );
        assert!(found.iter().all(|card| card.card == "card1"));
    }
}
