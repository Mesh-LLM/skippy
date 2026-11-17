pub(super) mod generator;
use crate::command::DynResult;

pub(crate) fn run(args: &[String]) -> DynResult<()> {
    let mut report = None;
    let mut pass = generator::Pass::First;
    let (pairs, tail) = args.as_chunks::<2>();
    if !tail.is_empty() {
        return Err("rewriter-report expects option/value pairs".into());
    }
    for [key, value] in pairs {
        match key.as_str() {
            "--report" => report = Some(std::path::Path::new(value)),
            "--mode" if value == "idempotence" => pass = generator::Pass::Second,
            "--mode" if value == "validate" => {}
            "--patch-drift-gate" if value == "fail" || value == "warn" => {}
            "--patch-check" | "--compile-result" | "--graph-verify-result"
                if value == "pass" || value == "skipped" => {}
            _ => return Err(format!("invalid or failed rewriter gate: {key}={value}").into()),
        }
    }
    println!(
        "{}",
        serde_json::to_string(&generator::load(report.ok_or("missing --report")?, pass)?)?
    );
    Ok(())
}
