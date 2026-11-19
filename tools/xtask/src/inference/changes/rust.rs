//! Discover native Rust modules and their embedded input files.
pub(super) use super::syntax::{planner_projection, possible_target, projection};
use super::{
    source::{Source, relative},
    syntax::parsed,
};
use crate::command::DynResult;
use quote::ToTokens;
use std::collections::BTreeSet;
use syn::{Attribute, Item, Meta, visit_mut::VisitMut};

pub(super) fn assets(
    source: &mut impl Source,
    revision: &str,
    paths: &BTreeSet<String>,
) -> DynResult<BTreeSet<String>> {
    #[derive(Default)]
    struct Includes(Vec<syn::Macro>);
    impl VisitMut for Includes {
        fn visit_macro_mut(&mut self, mac: &mut syn::Macro) {
            if mac.path.is_ident("include_str") || mac.path.is_ident("include_bytes") {
                self.0.push(mac.clone());
            }
        }
    }
    let mut assets = BTreeSet::new();
    for path in paths {
        let text = source
            .read(revision, path)?
            .ok_or("missing runtime source")?;
        let mut file = parsed(&text)?;
        let mut includes = Includes::default();
        includes.visit_file_mut(&mut file);
        for mac in includes.0 {
            let literal = syn::parse2::<syn::LitStr>(mac.tokens).map_err(|_| {
                format!("unresolved embedded asset in {path}; extend the input policy")
            })?;
            let directory = path.rsplit_once('/').map_or("", |(parent, _)| parent);
            assets.insert(relative(directory, &literal.value())?);
        }
    }
    Ok(assets)
}

pub(super) fn modules(
    source: &mut impl Source,
    revision: &str,
    roots: &[String],
) -> DynResult<BTreeSet<String>> {
    let mut paths = BTreeSet::new();
    for root in roots {
        visit(source, revision, root, &mut paths)?;
    }
    Ok(paths)
}

fn visit(
    source: &mut impl Source,
    revision: &str,
    path: &str,
    paths: &mut BTreeSet<String>,
) -> DynResult<()> {
    if !paths.insert(path.to_owned()) {
        return Ok(());
    }
    let text = source
        .read(revision, path)?
        .ok_or_else(|| format!("missing production module: {path}"))?;
    let file = parsed(&text)?;
    let parent = path.rsplit_once('/').map_or("", |(parent, _)| parent);
    let stem = path
        .rsplit('/')
        .next()
        .unwrap_or(path)
        .trim_end_matches(".rs");
    let directory = if matches!(stem, "lib" | "main" | "mod" | "build") {
        parent.to_owned()
    } else {
        relative(parent, stem)?
    };
    children(source, revision, &file.items, parent, &directory, paths)
}

fn children(
    source: &mut impl Source,
    revision: &str,
    items: &[Item],
    parent: &str,
    directory: &str,
    paths: &mut BTreeSet<String>,
) -> DynResult<()> {
    for item in items {
        if let Item::Mod(module) = item {
            if let Some((_, items)) = &module.content {
                children(
                    source,
                    revision,
                    items,
                    parent,
                    &relative(directory, &module.ident.to_string())?,
                    paths,
                )?;
                continue;
            }
            if module.attrs.iter().any(|attr| {
                attr.path().is_ident("cfg_attr")
                    && attr
                        .meta
                        .to_token_stream()
                        .to_string()
                        .split_whitespace()
                        .any(|token| token == "path")
            }) {
                return Err("conditional module path requires an explicit input policy".into());
            }
            let override_path = module
                .attrs
                .iter()
                .find(|attr| attr.path().is_ident("path"));
            let path = if let Some(Attribute {
                meta: Meta::NameValue(value),
                ..
            }) = override_path
            {
                let syn::Expr::Lit(syn::ExprLit {
                    lit: syn::Lit::Str(path),
                    ..
                }) = &value.value
                else {
                    return Err("unsupported module path attribute".into());
                };
                relative(parent, &path.value())?
            } else {
                let flat = relative(directory, &format!("{}.rs", module.ident))?;
                let nested = relative(directory, &format!("{}/mod.rs", module.ident))?;
                match (
                    source.read(revision, &flat)?.is_some(),
                    source.read(revision, &nested)?.is_some(),
                ) {
                    (true, false) => flat,
                    (false, true) => nested,
                    _ => {
                        return Err(format!(
                            "cannot resolve production module {} in {directory}",
                            module.ident
                        )
                        .into());
                    }
                }
            };
            visit(source, revision, &path, paths)?;
        }
        if let Item::Macro(item) = item
            && item.mac.path.is_ident("include")
            && let Ok(path) = syn::parse2::<syn::LitStr>(item.mac.tokens.clone())
        {
            visit(source, revision, &relative(parent, &path.value())?, paths)?;
        }
    }
    Ok(())
}
