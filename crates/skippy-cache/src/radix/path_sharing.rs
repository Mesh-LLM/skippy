//! How much of a token path the tree already holds, whatever its entries store.
use super::{RadixNode, UnifiedRadixCache, common_prefix_len};

fn shared_path_len<R, E>(root: &RadixNode<R, E>, tokens: &[i32]) -> usize {
    let mut node = root;
    let mut remaining = tokens;
    let mut consumed = 0usize;

    while let Some(first) = remaining.first() {
        let Some(child) = node.children.get(first) else {
            break;
        };
        let common = common_prefix_len(&child.edge, remaining);
        consumed = consumed.saturating_add(common);
        if common != child.edge.len() {
            break;
        }
        remaining = &remaining[common..];
        node = child;
    }
    consumed
}

impl<R, E> UnifiedRadixCache<R, E> {
    /// How many leading `tokens` some stored entry's path in `namespace`
    /// shares, resident and recurrent alike. Removal prunes every node that
    /// leads to no entry, so each matched edge is part of one. Reads without
    /// changing recency or references.
    pub fn shared_path_tokens(&self, namespace: &str, tokens: &[i32]) -> usize {
        self.roots
            .get(namespace)
            .map_or(0, |root| shared_path_len(root, tokens))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_path_counts_any_entry_kind_and_stops_inside_an_edge() {
        let mut tree = UnifiedRadixCache::<u32, u32>::new();
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 3]), 0);
        tree.insert_recurrent("model", &[1, 2, 3, 4, 5], 64, 5)
            .unwrap();
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 3, 9]), 3);
        assert_eq!(tree.shared_path_tokens("model", &[1, 2]), 2);
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 3, 4, 5, 6]), 5);
        tree.insert_resident("model", &[1, 2, 7, 8], 32, 4).unwrap();
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 7, 8, 9]), 4);
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 3, 9]), 3);
        assert_eq!(tree.shared_path_tokens("other", &[1, 2, 3]), 0);
    }

    #[test]
    fn removed_entries_stop_counting_as_shared() {
        let mut tree = UnifiedRadixCache::<u32, u32>::new();
        tree.insert_recurrent("model", &[1, 2, 3, 4], 64, 4)
            .unwrap();
        tree.insert_recurrent("model", &[1, 2], 32, 2).unwrap();
        assert_eq!(tree.remove_recurrent("model", &[1, 2, 3, 4]), Some(4));
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 3, 4]), 2);
        assert_eq!(tree.remove_recurrent("model", &[1, 2]), Some(2));
        assert_eq!(tree.shared_path_tokens("model", &[1, 2, 3, 4]), 0);
    }
}
