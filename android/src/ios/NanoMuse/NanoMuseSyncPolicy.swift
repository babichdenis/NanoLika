import Foundation
import CryptoKit

/// Pure rules for history-cap failures and retry-safe batching.
struct NanoMuseLookupBudget {
    private var remaining: Int
    init(maxPages: Int) { remaining = max(0, maxPages) }
    mutating func take() -> Bool {
        guard remaining > 0 else { return false }
        remaining -= 1
        return true
    }
}

struct NanoMuseSyncReceipt: Codable, Equatable {
    let cid: String
    let localDigest: String
    let remoteDigest: String
}

enum NanoMuseSyncPolicy {
    private static func digest(_ value: [String: Any]) -> String? {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Retain the precise remote meaning, including attachment metadata and reply parent.
    static func remoteDigest(_ remote: [String: Any]) -> String? {
        guard let mid = remote["mid"] as? String, let cid = remote["cid"] as? String,
              let role = remote["role"] as? String, let text = remote["text"] as? String,
              let created = remote["created_at"] as? Int,
              (remote["deleted"] as? Bool) != true, (remote["truncated"] as? Bool) != true else { return nil }
        let attachments = (remote["attachments"] as? [[String: Any]] ?? []).map { item in
            ["name": item["name"] as? String ?? "", "mime": item["mime"] as? String ?? "",
             "size": item["size"] as? Int ?? 0, "url": item["url"] as? String ?? ""] as [String: Any]
        }
        return digest(["mid": mid.lowercased(), "cid": cid, "role": role, "text": text,
                       "created_at": created, "reply_to_mid": remote["reply_to_mid"] as? String ?? "",
                       "attachments": attachments])
    }

    static func receipt(local: [String: Any], remoteDigest: String?) -> NanoMuseSyncReceipt? {
        guard let cid = local["cid"] as? String, !cid.isEmpty,
              let localDigest = digest(local), let remoteDigest, !remoteDigest.isEmpty else { return nil }
        return NanoMuseSyncReceipt(cid: cid, localDigest: localDigest, remoteDigest: remoteDigest)
    }

    static func trusts(receipt: NanoMuseSyncReceipt?, local: [String: Any], cid: String) -> Bool {
        guard let receipt, receipt.cid == cid, let localDigest = digest(local) else { return false }
        return localDigest == receipt.localDigest
    }

    static func trustsLive(receipt: NanoMuseSyncReceipt?, local: [String: Any], remote: [String: Any]) -> Bool {
        guard let cid = remote["cid"] as? String, let receipt,
              trusts(receipt: receipt, local: local, cid: cid),
              let remoteDigest = self.remoteDigest(remote) else { return false }
        return remoteDigest == receipt.remoteDigest
    }

    static func historyFull(status: Int, code: String) -> Bool {
        status == 409 && code == "message_limit"
    }

    static func syncDisabled(code: String) -> Bool {
        code == "sync_off"
    }

    static func ownsReply(expected: String, current: String, store: String) -> Bool {
        !expected.isEmpty && expected == current && expected == store
    }

    static func refused(mid: String, cid: String?, rejected: [[String: String]]) -> Bool {
        rejected.contains { $0["mid"] == mid || (cid != nil && $0["cid"] == cid) }
    }

    static func matches(local: [String: Any], remote: [String: Any]) -> Bool {
        if (local["deleted"] as? Bool) == true {
            guard let mid = local["mid"] as? String, let cid = local["cid"] as? String else { return false }
            return (remote["deleted"] as? Bool) == true && (remote["cid"] as? String) == cid
                && (remote["mid"] as? String)?.lowercased() == mid.lowercased()
        }
        guard let mid = local["mid"] as? String, let cid = local["cid"] as? String,
              let role = local["role"] as? String, let text = local["text"] as? String,
              let created = local["created_at"] as? Int,
              text.utf8.count <= 16384, (local["attachments"] as? [[String: Any]] ?? []).isEmpty,
              (remote["attachments"] as? [[String: Any]] ?? []).isEmpty,
              (remote["reply_to_mid"] as? String ?? "").isEmpty,
              (remote["deleted"] as? Bool) != true, (remote["truncated"] as? Bool) != true else { return false }
        return (remote["mid"] as? String)?.lowercased() == mid.lowercased()
            && (remote["cid"] as? String) == cid && (remote["role"] as? String) == role
            && (remote["text"] as? String) == text && (remote["created_at"] as? Int) == created
    }

    /// Deletions have their own POSTs: a full additions batch must not roll them
    /// back. The original order within each group remains unchanged.
    static func batches(deleted: [Bool], limit: Int) -> [[Int]] {
        guard limit > 0 else { return [] }
        let removals = deleted.indices.filter { deleted[$0] }
        let additions = deleted.indices.filter { !deleted[$0] }
        func chunk(_ indices: [Int]) -> [[Int]] {
            stride(from: 0, to: indices.count, by: limit).map {
                Array(indices[$0..<min($0 + limit, indices.count)])
            }
        }
        return chunk(removals) + chunk(additions)
    }
}
