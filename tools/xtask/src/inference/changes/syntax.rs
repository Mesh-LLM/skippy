//! Compare production syntax, retaining literals and removing only proven test items.
use crate::command::DynResult;
use proc_macro2::{TokenStream, TokenTree};
use quote::ToTokens;
use syn::{Attribute, Item, Meta, visit_mut::VisitMut};

fn attributes(item: &mut Item) -> Option<&mut Vec<Attribute>> {
    match item {
        Item::Const(i) => Some(&mut i.attrs),
        Item::Enum(i) => Some(&mut i.attrs),
        Item::ExternCrate(i) => Some(&mut i.attrs),
        Item::Fn(i) => Some(&mut i.attrs),
        Item::ForeignMod(i) => Some(&mut i.attrs),
        Item::Impl(i) => Some(&mut i.attrs),
        Item::Macro(i) => Some(&mut i.attrs),
        Item::Mod(i) => Some(&mut i.attrs),
        Item::Static(i) => Some(&mut i.attrs),
        Item::Struct(i) => Some(&mut i.attrs),
        Item::Trait(i) => Some(&mut i.attrs),
        Item::TraitAlias(i) => Some(&mut i.attrs),
        Item::Type(i) => Some(&mut i.attrs),
        Item::Union(i) => Some(&mut i.attrs),
        Item::Use(i) => Some(&mut i.attrs),
        _ => None,
    }
}

// Unknown cfg predicates remain possible. Only conditions proven false with
// `test = false` can remove code from the production comparison.
fn condition(meta: &Meta) -> DynResult<Option<bool>> {
    match meta {
        Meta::Path(path) if path.is_ident("test") || path.is_ident("windows") => Ok(Some(false)),
        Meta::Path(path) if path.is_ident("unix") => Ok(Some(true)),
        Meta::NameValue(value) => {
            let syn::Expr::Lit(syn::ExprLit {
                lit: syn::Lit::Str(literal),
                ..
            }) = &value.value
            else {
                return Ok(None);
            };
            let expected = if value.path.is_ident("target_os") {
                "linux"
            } else if value.path.is_ident("target_arch") {
                "aarch64"
            } else if value.path.is_ident("target_env") {
                "gnu"
            } else if value.path.is_ident("target_family") {
                "unix"
            } else {
                return Ok(None);
            };
            Ok(Some(literal.value() == expected))
        }
        Meta::List(list) => {
            use syn::parse::Parser;
            let children = syn::punctuated::Punctuated::<Meta, syn::Token![,]>::parse_terminated
                .parse2(list.tokens.clone())?;
            let values = children
                .iter()
                .map(condition)
                .collect::<DynResult<Vec<_>>>()?;
            if list.path.is_ident("all") {
                Ok(if values.contains(&Some(false)) {
                    Some(false)
                } else if values.iter().all(|value| *value == Some(true)) {
                    Some(true)
                } else {
                    None
                })
            } else if list.path.is_ident("any") {
                Ok(if values.contains(&Some(true)) {
                    Some(true)
                } else if values.iter().all(|value| *value == Some(false)) {
                    Some(false)
                } else {
                    None
                })
            } else if list.path.is_ident("not") && values.len() == 1 {
                Ok(values[0].map(|value| !value))
            } else {
                Ok(None)
            }
        }
        _ => Ok(None),
    }
}

fn test_only(attrs: &[Attribute]) -> bool {
    attrs.iter().any(|attr| {
        attr.path().is_ident("test")
            || (attr.path().is_ident("cfg")
                && attr
                    .parse_args::<Meta>()
                    .ok()
                    .and_then(|meta| condition(&meta).ok())
                    .flatten()
                    == Some(false))
    })
}

fn clean(attrs: &mut Vec<Attribute>) {
    attrs.retain(|attr| {
        if !attr.path().is_ident("cfg_attr") {
            return true;
        }
        use syn::parse::Parser;
        let Ok(arguments) = syn::punctuated::Punctuated::<Meta, syn::Token![,]>::parse_terminated
            .parse2(match &attr.meta {
                Meta::List(list) => list.tokens.clone(),
                _ => return true,
            })
        else {
            return true;
        };
        !arguments
            .first()
            .is_some_and(|predicate| condition(predicate).ok() == Some(Some(false)))
    });
}

struct Production;
impl VisitMut for Production {
    fn visit_item_mut(&mut self, item: &mut Item) {
        if let Some(attrs) = attributes(item) {
            clean(attrs);
        }
        syn::visit_mut::visit_item_mut(self, item);
    }
    fn visit_file_mut(&mut self, file: &mut syn::File) {
        if test_only(&file.attrs) {
            file.items.clear();
            file.attrs.clear();
            return;
        }
        file.items
            .retain_mut(|item| !attributes(item).is_some_and(|attrs| test_only(attrs)));
        syn::visit_mut::visit_file_mut(self, file);
    }
    fn visit_item_mod_mut(&mut self, module: &mut syn::ItemMod) {
        if let Some((_, items)) = &mut module.content {
            items.retain_mut(|item| !attributes(item).is_some_and(|attrs| test_only(attrs)));
        }
        syn::visit_mut::visit_item_mod_mut(self, module);
    }
    fn visit_block_mut(&mut self, block: &mut syn::Block) {
        block.stmts.retain_mut(|stmt| match stmt {
            syn::Stmt::Item(item) => !attributes(item).is_some_and(|attrs| test_only(attrs)),
            _ => true,
        });
        syn::visit_mut::visit_block_mut(self, block);
    }
    fn visit_item_impl_mut(&mut self, item: &mut syn::ItemImpl) {
        item.items.retain(|item| match item {
            syn::ImplItem::Fn(i) => !test_only(&i.attrs),
            syn::ImplItem::Const(i) => !test_only(&i.attrs),
            syn::ImplItem::Type(i) => !test_only(&i.attrs),
            _ => true,
        });
        syn::visit_mut::visit_item_impl_mut(self, item);
    }
    fn visit_fields_mut(&mut self, fields: &mut syn::Fields) {
        match fields {
            syn::Fields::Named(fields) => {
                fields.named = std::mem::take(&mut fields.named)
                    .into_iter()
                    .filter(|field| !test_only(&field.attrs))
                    .collect()
            }
            syn::Fields::Unnamed(fields) => {
                fields.unnamed = std::mem::take(&mut fields.unnamed)
                    .into_iter()
                    .filter(|field| !test_only(&field.attrs))
                    .collect()
            }
            syn::Fields::Unit => {}
        }
        syn::visit_mut::visit_fields_mut(self, fields);
    }
    fn visit_item_enum_mut(&mut self, item: &mut syn::ItemEnum) {
        item.variants = std::mem::take(&mut item.variants)
            .into_iter()
            .filter(|variant| !test_only(&variant.attrs))
            .collect();
        syn::visit_mut::visit_item_enum_mut(self, item);
    }
}

pub(super) fn parsed(text: &str) -> DynResult<syn::File> {
    let mut file = syn::parse_file(text)?;
    Production.visit_file_mut(&mut file);
    Ok(file)
}

pub(super) fn possible_target(target: &str) -> DynResult<bool> {
    if !target.starts_with("cfg(") {
        return Ok(target == "aarch64-unknown-linux-gnu");
    }
    let meta: Meta = syn::parse_str(target)?;
    let Meta::List(list) = meta else {
        return Err("invalid target condition".into());
    };
    Ok(condition(&syn::parse2::<Meta>(list.tokens)?)? != Some(false))
}

fn without_docs(tokens: TokenStream) -> TokenStream {
    let mut output = TokenStream::new();
    let mut tokens = tokens.into_iter().peekable();
    while let Some(token) = tokens.next() {
        if matches!(&token, TokenTree::Punct(p) if p.as_char() == '#') {
            let mut probe = tokens.clone();
            if matches!(probe.peek(), Some(TokenTree::Punct(p)) if p.as_char() == '!') {
                probe.next();
            }
            if let Some(TokenTree::Group(group)) = probe.next()
                && group.delimiter() == proc_macro2::Delimiter::Bracket
                && matches!(group.stream().into_iter().next(), Some(TokenTree::Ident(i)) if i == "doc")
            {
                tokens = probe;
                continue;
            }
        }
        if let TokenTree::Group(group) = token {
            output.extend([TokenTree::Group(proc_macro2::Group::new(
                group.delimiter(),
                without_docs(group.stream()),
            ))]);
        } else {
            output.extend([token]);
        }
    }
    output
}

pub(super) fn projection(text: &str) -> DynResult<String> {
    Ok(without_docs(parsed(text)?.into_token_stream()).to_string())
}

pub(super) fn planner_projection(text: &str) -> DynResult<String> {
    let mut file = parsed(text)?;
    file.items.retain(|item| match item {
        Item::Fn(function) => matches!(function.sig.ident.to_string().as_str(), "plan" | "run"),
        Item::Struct(_) | Item::Enum(_) | Item::Use(_) => true,
        _ => false,
    });
    struct WorkerCommands;
    impl VisitMut for WorkerCommands {
        fn visit_expr_match_mut(&mut self, expression: &mut syn::ExprMatch) {
            expression.arms.retain(|arm| {
                let syn::Pat::Slice(slice) = &arm.pat else {
                    return true;
                };
                let Some(syn::Pat::Lit(literal)) = slice.elems.first() else {
                    return true;
                };
                let syn::Lit::Str(command) = &literal.lit else {
                    return true;
                };
                matches!(command.value().as_str(), "build" | "execute")
            });
            syn::visit_mut::visit_expr_match_mut(self, expression);
        }
    }
    for item in &mut file.items {
        if let Item::Fn(function) = item
            && function.sig.ident == "run"
        {
            WorkerCommands.visit_block_mut(&mut function.block);
        }
    }
    Ok(without_docs(file.into_token_stream()).to_string())
}
