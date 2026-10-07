use std::collections::BTreeMap;

use crate::version::{Version, coerce_version};
use crate::param_sets::{ParamSets, param_sets_for_firmware, parse_param_sets};

const PARAMS_V1: &str = include_str!("tests/fixtures/params_v1.json");

fn sample_param_sets() -> ParamSets {
    parse_param_sets(PARAMS_V1).expect("fixture should parse")
}

fn selected_keys(vehicle_type: &str, version: Version, board: &str) -> Vec<String> {
    param_sets_for_firmware(&sample_param_sets(), vehicle_type, &version, board).into_keys().collect()
}

fn v(major: u32, minor: u32, patch: u32) -> Option<Version> {
    Some(Version {
        major,
        minor,
        patch,
    })
}

#[test]
fn coerces_partial_versions() {
    assert_eq!(coerce_version("4.2"), v(4, 2, 0));
    assert_eq!(coerce_version("v4.2"), v(4, 2, 0));
    assert_eq!(coerce_version("4"), v(4, 0, 0));
    assert_eq!(coerce_version("4."), v(4, 0, 0));
    assert_eq!(coerce_version("4..5"), v(4, 0, 0));
    assert_eq!(coerce_version("04.05"), v(4, 5, 0));
}

#[test]
fn coerces_full_versions() {
    assert_eq!(coerce_version("4.5.1"), v(4, 5, 1));
    assert_eq!(coerce_version("STABLE-4.5.1"), v(4, 5, 1));
    assert_eq!(coerce_version("4.5.1-beta"), v(4, 5, 1));
}

#[test]
fn coerces_extended_versions() {
    assert_eq!(coerce_version("4.5.1.4"), v(4, 5, 1));
}

#[test]
fn invalid_versions() {
    assert_eq!(coerce_version("BETA"), None);
    assert_eq!(coerce_version(""), None);
    assert_eq!(coerce_version("99999999999"), None);
}

#[test]
fn returns_values_of_matching_set() {
    assert_eq!(
        selected_keys("ArduSub", v(4, 5, 0).unwrap(), "pixhawk1"),
        vec![
            "params/ardupilot/ArduSub/4.5/pixhawk1/Standard BlueROV2.params"
        ]
    );
}

#[test]
fn returns_every_set_at_newest_version() {
    assert_eq!(
        selected_keys("ArduSub", v(4, 5, 2).unwrap(), "Navigator"),
        vec![
            "params/ardupilot/ArduSub/4.5/navigator/Heavy BlueROV2.params",
            "params/ardupilot/ArduSub/4.5/navigator/Standard BlueROV2.params",
        ]
    );
}

#[test]
fn carries_newest_older_set_forward() {
    assert_eq!(
        selected_keys("ArduSub", v(4, 6, 1).unwrap(), "Navigator"),
        vec!["params/ardupilot/ArduSub/4.6/navigator/Standard BlueROV2.params"]
    );
    assert_eq!(
        selected_keys("ArduSub", v(4, 4, 1).unwrap(), "Navigator"),
        vec!["params/ardupilot/ArduSub/4.4/navigator/Standard BlueROV2.params"]
    );
}

#[test]
fn skips_sets_newer_than_firmware() {
    assert!(selected_keys("ArduSub", v(4, 3, 0).unwrap(), "Navigator").is_empty());
}

#[test]
fn stays_within_firmware_major() {
    assert!(selected_keys("ArduSub", v(5, 0, 0).unwrap(), "Navigator").is_empty());
    assert_eq!(
        selected_keys("ArduSub", v(3, 6, 2).unwrap(), "Navigator"),
        vec!["params/ardupilot/ArduSub/3.6/navigator/Standard BlueROV2.params"]
    );
}

#[test]
fn matches_vehicle_type() {
    assert_eq!(
        selected_keys("Rover", v(4, 5, 0).unwrap(), "Navigator"),
        vec!["params/ardupilot/Rover/4.5/navigator/BlueBoat.params"]
    );
}

#[test]
fn matches_board() {
    assert_eq!(
        selected_keys("ArduSub", v(4, 5, 2).unwrap(), "Pixhawk1"),
        vec!["params/ardupilot/ArduSub/4.5/pixhawk1/Standard BlueROV2.params"]
    );
    assert!(selected_keys("ArduSub", v(4, 5, 2).unwrap(), "Unknown").is_empty());
}

#[test]
fn maps_sitl_board_to_sitl_directory() {
    assert_eq!(
        selected_keys("ArduSub", v(4, 5, 2).unwrap(), "SITL_x86_64_linux_gnu"),
        vec!["params/ardupilot/ArduSub/4.5/sitl/Standard BlueROV2.params"]
    );
}

#[test]
fn parses_valid_fixture() {
    let parsed = parse_param_sets(PARAMS_V1).unwrap();
    assert_eq!(parsed.len(), 9);
    assert_eq!(parsed["params/ardupilot/ArduSub/4.5/navigator/Heavy BlueROV2.params"]["FRAME_CONFIG"], 2.0);
}

#[test]
fn parses_empty_object() {
    assert_eq!(parse_param_sets("{}").unwrap(), BTreeMap::new());
}

#[test]
fn rejects_non_numeric_value() {
    assert!(parse_param_sets(r#"{ "key": { "FRAME_CONFIG": "abc" } }"#).is_err());
}

#[test]
fn rejects_non_object() {
    assert!(parse_param_sets("[]").is_err());
    assert!(parse_param_sets("\"text\"").is_err());
}

#[test]
fn rejects_broken_json() {
    assert!(parse_param_sets("{").is_err());
}
