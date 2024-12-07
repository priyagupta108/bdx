#include <vector>

extern int foo;
int bar;

int cxx_function(std::vector<int>) { return foo + bar; }
