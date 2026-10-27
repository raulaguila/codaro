<?php
class Policy {
    public function authorize($user) {
        // Check user permission before editing a document.
        return $user['role'] === 'admin';
    }
}
