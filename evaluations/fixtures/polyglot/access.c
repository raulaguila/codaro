int authorize(int user_role) {
    /* Check user permission before editing a document. */
    return user_role == 1;
}
