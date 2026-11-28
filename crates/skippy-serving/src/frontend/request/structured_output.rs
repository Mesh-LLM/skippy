//! Translate public response formats into the native sampling constraints.
use super::{
    GenerationRequest, InferenceError, InferenceResult, Value, bounded_optional_string_extra,
    serialize_bounded_native_parser_json,
};

pub(super) fn template_constraints(
    request: &GenerationRequest,
) -> InferenceResult<(Option<String>, Option<String>)> {
    let grammar = bounded_optional_string_extra(&request.extra, "grammar")?;
    let native_schema = request.extra.get("json_schema").filter(|v| !v.is_null());
    let public_schema = response_schema(request.response_format.as_ref())?;
    if public_schema.is_some() && (grammar.is_some() || native_schema.is_some()) {
        return Err(InferenceError::invalid_request(
            "response_format cannot be combined with grammar or json_schema",
        ));
    }
    let schema = public_schema.as_ref().or(native_schema);
    if grammar.is_some() && schema.is_some() {
        return Err(InferenceError::invalid_request(
            "grammar and json_schema cannot both be set",
        ));
    }
    if let Some(grammar) = grammar.as_deref() {
        crate::grammar_bounds::check_grammar(grammar).map_err(InferenceError::invalid_request)?;
    }
    let schema = schema
        .map(|schema| {
            if !schema.is_object() {
                return Err(InferenceError::invalid_request(
                    "json_schema must be an object",
                ));
            }
            serialize_bounded_native_parser_json("json_schema", schema)
        })
        .transpose()?;
    Ok((grammar, schema))
}

fn response_schema(format: Option<&Value>) -> InferenceResult<Option<Value>> {
    let Some(format) = format.filter(|v| !v.is_null()) else {
        return Ok(None);
    };
    match format.get("type").and_then(Value::as_str) {
        Some("text") => Ok(None),
        Some("json_object") => Ok(Some(serde_json::json!({"type":"object"}))),
        Some("json_schema") => {
            let specification = format
                .get("json_schema")
                .and_then(Value::as_object)
                .ok_or_else(|| {
                    InferenceError::invalid_request("response_format.json_schema must be an object")
                })?;
            if specification
                .get("strict")
                .is_some_and(|v| !v.is_boolean() && !v.is_null())
            {
                return Err(InferenceError::invalid_request(
                    "response_format.json_schema.strict must be a boolean",
                ));
            }
            let schema = specification
                .get("schema")
                .filter(|v| v.is_object())
                .ok_or_else(|| {
                    InferenceError::invalid_request(
                        "response_format.json_schema.schema must be an object",
                    )
                })?;
            Ok(Some(schema.clone()))
        }
        _ => Err(InferenceError::invalid_request(
            "response_format.type must be text, json_object, or json_schema",
        )),
    }
}
