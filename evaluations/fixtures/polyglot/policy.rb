class Policy
  def authorize(user)
    # Check user permission before editing a document.
    user[:role] == 'admin'
  end
end
