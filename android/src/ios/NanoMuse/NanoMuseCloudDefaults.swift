import Foundation

/// A nonempty group can still contain only deleted, hidden or disabled entries.
/// Keep a working choice; repair only when none of its members is usable here.
enum NanoMuseCloudDefaults {
    static func needsRepair(members: [String]?, available: Set<String>) -> Bool {
        guard let members else { return true }
        return !members.contains { available.contains($0) }
    }
}
