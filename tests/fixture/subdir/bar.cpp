#include <vector>

extern int foo;
int bar;

int cxx_function(std::vector<int>) { return foo + bar; }

char CppCamelCaseSymbol(const char *x) { return x[0]; }
